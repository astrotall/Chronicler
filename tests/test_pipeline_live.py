import json
import logging
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.bot.formatting import post_ready_messages
from app.config.constants import FACT_CANDIDATES_MAX, LLMStep
from app.config.settings import Settings
from app.domain.draft import PostFormat
from app.domain.llm import LLMResult, Message
from app.domain.pipeline import PipelineStage, PostAction, PostReady
from app.domain.research import ResearchResult
from app.domain.snippet import Snippet
from app.llm.client import LLMClient
from app.llm.factory import LLMClientFactory, build_http_client
from app.research.factory import build_sources
from app.research.http import build_research_http_client
from app.research.source import ResearchSource
from app.services.facts import (
    ExtractedFacts,
    FactLimits,
    fit_to_budget,
    normalize_label,
    snippet_alias,
    verify_candidate,
)
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.research import ResearchLimits, ResearchService
from app.services.run_store import InMemoryRunStore
from pydantic import BaseModel

from fact_snapshot import fact_set_entry
from live_keys import LiveKeys, skip_without_key

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
TOPICS: dict[str, str] = {
    "soviet_day_1930s": "Как выглядел обычный день советского человека в 1930-е?",
    "kulikovo": "Куликовская битва",
}
LLM_LOGGER = "app.llm"

pytestmark = [
    pytest.mark.integration,
    skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key),
    skip_without_key("TAVILY_API_KEY", KEYS.tavily_api_key),
]


class LLMCallLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []
        self.invalid_json = 0
        self.retries = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("llm call "):
            fields: dict[str, object] = {}
            for part in message.split()[2:]:
                name, _, value = part.partition("=")
                fields[name] = value
            self.calls.append(fields)
        elif message.startswith("llm invalid json"):
            self.invalid_json += 1
        elif message.startswith("llm retry"):
            self.retries += 1


class CapturingClient:
    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.replies: list[BaseModel] = []

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None, max_tokens: int
    ) -> LLMResult:
        return await self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        reply = await self._inner.complete_json(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )
        self.replies.append(reply)
        return reply


class CountingSource:
    def __init__(self, inner: ResearchSource) -> None:
        self._inner = inner
        self.name = inner.name
        self.calls = 0

    async def search(self, query: str) -> list[Snippet]:
        self.calls += 1
        return await self._inner.search(query)


class RecordingResearcher:
    def __init__(self, inner: ResearchService) -> None:
        self._inner = inner
        self.queries: list[str] = []
        self.results: list[ResearchResult] = []

    @property
    def source_names(self) -> list[str]:
        return self._inner.source_names

    async def research(self, queries: Sequence[str]) -> ResearchResult:
        self.queries = list(queries)
        result = await self._inner.research(queries)
        self.results.append(result)
        return result


class StageTimer:
    def __init__(self) -> None:
        self.marks: list[tuple[str, float]] = []

    async def __call__(self, stage: PipelineStage) -> None:
        self.marks.append((stage.value, time.monotonic()))


def support_by_origin(
    reply: ExtractedFacts, snippets: Sequence[Snippet], limits: FactLimits
) -> dict[str, dict[str, int]]:
    shown, _ = fit_to_budget(snippets, limits.input_max_chars)
    by_alias = {snippet_alias(index + 1): snippet for index, snippet in enumerate(shown)}
    origins = {alias: snippet.origin.value for alias, snippet in by_alias.items()}
    counts: dict[str, Counter[str]] = {}
    for candidate in reply.facts[:FACT_CANDIDATES_MAX]:
        check = verify_candidate(candidate, by_alias, limits)
        for item, verdict in zip(candidate.support, check.support_verdicts, strict=True):
            origin = origins.get(normalize_label(item.snippet), "unknown")
            counts.setdefault(origin, Counter())[verdict.value] += 1
    return {origin: dict(counter) for origin, counter in counts.items()}


def save_report(slug: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"his8_{slug}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ready(outcome: object) -> PostReady:
    assert isinstance(outcome, PostReady), type(outcome).__name__
    return outcome


def summary(result: PostReady) -> dict[str, object]:
    report = result.result.report
    return {
        "format": result.result.draft.post_format.value,
        "rendered": result.result.draft.rendered,
        "lengths": [len(part) for part in result.result.draft.rendered],
        "used_fact_ids": result.result.draft.used_fact_ids,
        "unverified_numbers": result.result.draft.unverified_numbers,
        "length_violations": [
            v.model_dump(mode="json") for v in result.result.draft.length_violations
        ],
        "violations": [v.model_dump(mode="json") for v in report.violations],
        "critic": report.critic.value,
        "attempts": result.result.attempts,
        "chosen_attempt": result.result.chosen_attempt,
        "regenerations": result.result.regenerations,
        "regeneration_failed": result.result.regeneration_failed,
        "downgrade": result.downgrade.model_dump() if result.downgrade else None,
        "messages": [
            {"html": message.html, "text": message.text} for message in post_ready_messages(result)
        ],
    }


@pytest.mark.parametrize("slug", list(TOPICS))
async def test_live_topic_to_post_and_thread(slug: str) -> None:
    settings = Settings()
    if settings.wikipedia_contact is None:
        pytest.skip("WIKIPEDIA_CONTACT is not set")
    calls = LLMCallLog()
    logging.getLogger(LLM_LOGGER).addHandler(calls)
    try:
        async with (
            build_http_client(settings) as llm_http,
            build_research_http_client(settings) as research_http,
        ):
            factory = LLMClientFactory(settings, llm_http)
            extractor = CapturingClient(factory.get_client(LLMStep.FACT_EXTRACTION))
            sources = [CountingSource(source) for source in build_sources(settings, research_http)]
            researcher = RecordingResearcher(
                ResearchService(sources, ResearchLimits.from_settings(settings))
            )
            limits = PipelineLimits.from_settings(settings)
            pipeline = Pipeline(
                PipelineClients(
                    planner=factory.get_client(LLMStep.QUERY_PLANNING),
                    extractor=extractor,
                    writer=factory.get_client(LLMStep.WRITING),
                    critic=factory.get_client(LLMStep.STYLE_CRITIQUE),
                ),
                researcher,
                InMemoryRunStore(settings.state_max_runs),
                limits,
            )
            timer = StageTimer()
            started = time.monotonic()
            first = ready(await pipeline.run_topic(TOPICS[slug], None, timer))
            first_seconds = time.monotonic() - started
            first_calls = len(calls.calls)
            started = time.monotonic()
            thread = await pipeline.rework(first.draft_id, PostAction.THREAD, StageTimer())
            thread_seconds = time.monotonic() - started
    finally:
        logging.getLogger(LLM_LOGGER).removeHandler(calls)

    [research] = researcher.results
    extraction_reply = next(r for r in extractor.replies if isinstance(r, ExtractedFacts))
    statuses = Counter(fact.status.value for fact in first.fact_set.facts)
    report = {
        "topic": TOPICS[slug],
        "queries": researcher.queries,
        "search_calls": {source.name: source.calls for source in sources},
        "snippets_by_origin": dict(Counter(snippet.origin.value for snippet in research.snippets)),
        "snippet_chars": sum(len(snippet.text) for snippet in research.snippets),
        "failures": [failure.model_dump() for failure in research.failures],
        "stats": first.stats.model_dump() if first.stats else None,
        **fact_set_entry(first.fact_set),
        "fact_statuses": dict(statuses),
        "confirmed_domains": [
            sorted({ref.domain for ref in fact.support})
            for fact in first.fact_set.facts
            if fact.status.value == "confirmed"
        ],
        "support_by_origin": support_by_origin(extraction_reply, research.snippets, limits.facts),
        "llm_calls_total": len(calls.calls),
        "llm_calls_first_post": first_calls,
        "llm_calls_by_step": dict(Counter(str(call["step"]) for call in calls.calls)),
        "llm_truncated": sum(1 for call in calls.calls if call["truncated"] == "True"),
        "llm_output_tokens_max_by_step": {
            step: max(
                int(str(call["output_tokens"])) for call in calls.calls if call["step"] == step
            )
            for step in {str(call["step"]) for call in calls.calls}
        },
        "llm_invalid_json": calls.invalid_json,
        "llm_retries": calls.retries,
        "seconds_first_post": round(first_seconds, 1),
        "seconds_thread": round(thread_seconds, 1),
        "stage_marks": [(name, round(mark - timer.marks[0][1], 1)) for name, mark in timer.marks],
        "post": summary(first),
        "thread": summary(thread)
        if isinstance(thread, PostReady)
        else {"outcome": type(thread).__name__},
    }
    save_report(slug, report)

    assert first.result.draft.post_format is PostFormat(settings.post_default_format)
    if isinstance(thread, PostReady):
        assert thread.result.draft.post_format is PostFormat.THREAD
    assert first.fact_set.facts
