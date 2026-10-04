import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.config.constants import LLMStep
from app.config.settings import Settings
from app.domain.draft import PostFormat
from app.domain.fact import FactExtraction
from app.domain.llm import LLMResult, Message
from app.domain.pipeline import PostAction, PostReady
from app.llm.client import LLMClient
from app.llm.factory import LLMClientFactory, build_http_client
from app.prompts.fact_relevance import RELEVANCE_LINE_SEPARATOR
from app.services.fact_relevance import RelevanceReport
from app.services.facts import FactLimits, extract_facts
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.run_store import InMemoryRunStore
from app.services.short_post import select_short_facts
from pydantic import BaseModel

from live_keys import LiveKeys, skip_without_key
from test_domain_trust_live import RecordedResearcher
from test_pipeline_live import LLM_LOGGER, LLMCallLog, StageTimer, summary
from test_stance_live import DictReplayClient, RecordedReply, RecordedResearch, planner_replies

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
RESEARCH_FILE = "his32_research_{slug}.json"
RECORDED_FILE = "{run}_{slug}.json"
REPORT_FILE = "his27_{slug}.json"
EXTRACTOR_REPLIES_KEY = "extractor_replies"
FACTS_HEADER = "Facts:\n"
ID_SEPARATOR = ": "
SLUGS = ("soviet_day_1930s", "kulikovo")
RECORDED_RUNS = ("his32_before", "his32_after2", "his32_after")
TEXT_RUN = {"soviet_day_1930s": "his32_after2", "kulikovo": "his32_after"}

pytestmark = [
    pytest.mark.integration,
    skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key),
]


class RankingLiveClient:
    def __init__(self, recorded: Sequence[RecordedReply], live: LLMClient) -> None:
        self._replay = DictReplayClient(recorded)
        self._live = live
        self.ranking: RelevanceReport | None = None
        self.ranking_prompt: list[Message] = []
        self.live_calls = 0

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None, max_tokens: int
    ) -> LLMResult:
        raise AssertionError("the extractor serves JSON replies only")

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        if schema is not RelevanceReport:
            return await self._replay.complete_json(
                messages, schema, temperature=temperature, max_tokens=max_tokens
            )
        if self.ranking is None:
            self.ranking_prompt = list(messages)
            self.live_calls += 1
            self.ranking = await self._live.complete_json(
                messages, RelevanceReport, temperature=temperature, max_tokens=max_tokens
            )
        return schema.model_validate(self.ranking.model_dump())


def read_json(name: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads((OUTPUT_DIR / name).read_text(encoding="utf-8"))
    return loaded


def write_report(slug: str, output: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / REPORT_FILE.format(slug=slug)).write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def recorded_replies(slug: str, run: str) -> list[RecordedReply]:
    raw = read_json(RECORDED_FILE.format(run=run, slug=slug))[EXTRACTOR_REPLIES_KEY]
    assert isinstance(raw, list)
    return [RecordedReply.model_validate(item) for item in raw]


def fact_rows(extraction: FactExtraction) -> list[dict[str, object]]:
    return [
        {
            "id": fact.id,
            "text": fact.text,
            "status": fact.status.value,
            "stance": fact.stance.value,
            "domains": list(dict.fromkeys(ref.domain for ref in fact.support)),
            "weak_only": fact.weak_only,
        }
        for fact in extraction.fact_set.facts
    ]


def prompt_texts(messages: Sequence[Message]) -> dict[str, str]:
    if not messages:
        return {}
    body = messages[-1].content.split(FACTS_HEADER, 1)[1]
    texts: dict[str, str] = {}
    for line in body.split(RELEVANCE_LINE_SEPARATOR):
        fact_id, _, text = line.partition(ID_SEPARATOR)
        texts[fact_id] = text
    return texts


def ranking_rows(client: RankingLiveClient, after: FactExtraction) -> list[dict[str, object]]:
    if client.ranking is None:
        return []
    texts = prompt_texts(client.ranking_prompt)
    kept = {fact.text for fact in after.fact_set.facts}
    return [
        {
            "id": entry.id,
            "text": texts.get(entry.id),
            "aspect": entry.aspect,
            "about_source": entry.about_source,
            "outside_period": entry.outside_period,
            "relevance": entry.relevance,
            "kept": texts.get(entry.id) in kept,
        }
        for entry in client.ranking.facts
    ]


def set_aside(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "text": row["text"],
            "reasons": [
                reason
                for reason, flagged in (
                    ("about_source", row["about_source"]),
                    ("outside_period", row["outside_period"]),
                    ("relevance 0", row["relevance"] == 0),
                )
                if flagged
            ],
            "returned_by_floor": row["kept"],
        }
        for row in rows
        if row["about_source"] or row["outside_period"] or row["relevance"] == 0
    ]


def short_ids(extraction: FactExtraction, max_facts: int) -> list[str]:
    return [fact.id for fact in select_short_facts(extraction.fact_set, max_facts).facts]


async def compare(
    slug: str,
    run: str,
    research: RecordedResearch,
    live: LLMClient,
    limits: FactLimits,
    short_max_facts: int,
) -> tuple[dict[str, object], RankingLiveClient]:
    replies = recorded_replies(slug, run)
    snippets = research.research.snippets
    before = await extract_facts(
        DictReplayClient(replies),
        research.topic,
        snippets,
        limits.model_copy(update={"relevance": False}),
    )
    client = RankingLiveClient(replies, live)
    after = await extract_facts(client, research.topic, snippets, limits)
    rows = ranking_rows(client, after)
    report: dict[str, object] = {
        "run": run,
        "before": {
            "outcome": before.outcome.value,
            "stats": before.stats.model_dump(),
            "facts": fact_rows(before),
            "short": short_ids(before, short_max_facts),
        },
        "after": {
            "outcome": after.outcome.value,
            "stats": after.stats.model_dump(),
            "facts": fact_rows(after),
            "short": short_ids(after, short_max_facts),
        },
        "ranking": rows,
        "set_aside": set_aside(rows),
        "ranking_calls": client.live_calls,
    }
    return report, client


async def write_texts(
    research: RecordedResearch,
    extractor: RankingLiveClient,
    factory: LLMClientFactory,
    settings: Settings,
) -> dict[str, object]:
    pipeline = Pipeline(
        PipelineClients(
            planner=DictReplayClient(planner_replies(research)),
            extractor=extractor,
            writer=factory.get_client(LLMStep.WRITING),
            critic=factory.get_client(LLMStep.STYLE_CRITIQUE),
        ),
        RecordedResearcher(research.research),
        InMemoryRunStore(settings.state_max_runs),
        PipelineLimits.from_settings(settings),
    )
    timer = StageTimer()
    short = await pipeline.run_topic(research.topic, PostFormat.SHORT, timer)
    assert isinstance(short, PostReady)
    thread = await pipeline.rework(short.draft_id, PostAction.THREAD, timer)
    long = await pipeline.run_topic(research.topic, PostFormat.LONG, timer)
    texts: dict[str, object] = {"short": summary(short)}
    texts["thread"] = summary(thread) if isinstance(thread, PostReady) else type(thread).__name__
    texts["long"] = summary(long) if isinstance(long, PostReady) else type(long).__name__
    return texts


@pytest.mark.parametrize("slug", SLUGS)
async def test_live_relevance_before_and_after(slug: str) -> None:
    research_path = OUTPUT_DIR / RESEARCH_FILE.format(slug=slug)
    if not research_path.exists():
        pytest.skip(f"no recorded research in {research_path}")
    settings = Settings()
    research = RecordedResearch.model_validate_json(research_path.read_text(encoding="utf-8"))
    limits = FactLimits.from_settings(settings)
    calls = LLMCallLog()
    llm_logger = logging.getLogger(LLM_LOGGER)
    llm_logger.setLevel(logging.INFO)
    llm_logger.addHandler(calls)
    try:
        async with build_http_client(settings) as llm_http:
            factory = LLMClientFactory(settings, llm_http)
            live = factory.get_client(LLMStep.FACT_EXTRACTION)
            runs: list[dict[str, object]] = []
            text_client: RankingLiveClient | None = None
            for run in RECORDED_RUNS:
                report, client = await compare(
                    slug, run, research, live, limits, settings.short_max_facts
                )
                runs.append(report)
                if run == TEXT_RUN[slug]:
                    text_client = client
            assert text_client is not None
            texts = await write_texts(research, text_client, factory, settings)
    finally:
        llm_logger.removeHandler(calls)
    by_step: dict[str, int] = {}
    for call in calls.calls:
        step = str(call.get("step"))
        by_step[step] = by_step.get(step, 0) + 1
    output = {
        "topic": research.topic,
        "queries": research.queries,
        "snippets": len(research.research.snippets),
        "runs": runs,
        "text_run": TEXT_RUN[slug],
        "texts": texts,
        "llm_calls_total": len(calls.calls),
        "llm_calls_by_step": by_step,
        "tavily_credits": 0,
    }
    write_report(slug, output)
    for report in runs:
        after = report["after"]
        assert isinstance(after, dict)
        assert after["outcome"] == "extracted"
