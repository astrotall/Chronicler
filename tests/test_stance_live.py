import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.config.constants import (
    FACT_CANDIDATES_MAX,
    FACT_EXTRACTION_MAX_TOKENS,
    QUERY_COUNT_MIN,
    LLMStep,
)
from app.config.settings import Settings
from app.domain.fact import ClaimStance, FactExtraction
from app.domain.llm import LLMResult, Message
from app.domain.pipeline import PostAction, PostReady
from app.domain.research import ResearchResult
from app.domain.snippet import Snippet
from app.llm.factory import LLMClientFactory, build_http_client
from app.research.factory import build_sources
from app.research.http import build_research_http_client
from app.services.facts import (
    CandidateCheck,
    ExtractedFacts,
    FactLimits,
    extract_facts,
    fit_to_budget,
    snippet_alias,
    verify_reply,
)
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.query_planning import QueryPlan, plan_queries
from app.services.research import ResearchLimits, ResearchService
from app.services.run_store import InMemoryRunStore
from app.services.stance import (
    locate_quote,
    parse_stance,
    quote_context,
    stance_evidence,
    strong_evidence,
)
from pydantic import BaseModel, ConfigDict, Field

from live_keys import LiveKeys, skip_without_key
from test_domain_trust_live import RecordedResearcher, ReplayClient
from test_pipeline_live import LLM_LOGGER, CapturingClient, LLMCallLog, StageTimer, summary

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
RESEARCH_FILE = "his32_research_{slug}.json"
BEFORE_FILE = "his32_before_{slug}.json"
AFTER_FILE = "his32_after_{slug}.json"
FEB30_QUERY = "30 февраля 1930 1931 СССР календарь"
CASES: dict[str, tuple[str, list[str] | None, bool]] = {
    "feb30": (FEB30_QUERY, [FEB30_QUERY], True),
    "soviet_day_1930s": ("Как выглядел обычный день советского человека в 1930-е?", None, False),
    "kulikovo": ("Куликовская битва", None, False),
}

pytestmark = [
    pytest.mark.integration,
    skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key),
]


class RecordedReply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    reply: dict[str, object]


class RecordedResearch(BaseModel):
    topic: str
    queries: list[str]
    planner_replies: list[RecordedReply]
    research: ResearchResult


class RecordedBefore(BaseModel):
    extractor_replies: list[RecordedReply]


class DictReplayClient:
    def __init__(self, replies: Sequence[RecordedReply]) -> None:
        self._replies = list(replies)

    async def complete(
        self, messages: Sequence[Message], *, temperature: float | None = None, max_tokens: int
    ) -> LLMResult:
        raise AssertionError("replay client serves JSON replies only")

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        for reply in self._replies:
            if reply.schema_name == schema.__name__:
                return schema.model_validate(reply.reply)
        raise AssertionError(f"no recorded reply for {schema.__name__}")


def planner_replies(recorded: RecordedResearch) -> list[RecordedReply]:
    if recorded.planner_replies:
        return recorded.planner_replies
    padded = [*recorded.queries]
    padded += [f"{recorded.queries[0]} {index}" for index in range(QUERY_COUNT_MIN - len(padded))]
    return [RecordedReply(schema_name=QueryPlan.__name__, reply={"queries": padded})]


def read_text(name: str) -> str | None:
    path = OUTPUT_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def write_json(name: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def shown_snippets(snippets: Sequence[Snippet], limits: FactLimits) -> dict[str, Snippet]:
    shown, _ = fit_to_budget(snippets, limits.input_max_chars)
    return {snippet_alias(index + 1): snippet for index, snippet in enumerate(shown)}


def stance_rows(
    extraction: FactExtraction, snippets: dict[str, Snippet]
) -> list[dict[str, object]]:
    texts = {snippet.id: snippet.text for snippet in snippets.values()}
    rows: list[dict[str, object]] = []
    for fact in extraction.fact_set.facts:
        if not fact.attributed:
            continue
        evidence = [
            {
                "domain": ref.domain,
                "marker": found.marker,
                "context": found.context,
            }
            for ref in fact.support
            if ref.snippet_id in texts
            and (found := stance_evidence(fact.stance, ref.quote, texts[ref.snippet_id]))
        ]
        rows.append(
            {
                "id": fact.id,
                "stance": fact.stance.value,
                "status": fact.status.value,
                "text": fact.text,
                "rebutted_by": [
                    {"id": other.id, "text": other.text}
                    for other in extraction.fact_set.facts
                    if other.id in fact.rebutted_by
                ],
                "evidence": evidence,
            }
        )
    return rows


def check_row(check: CandidateCheck, texts: dict[str, str]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for ref in check.support:
        text = texts.get(ref.snippet_id)
        if text is None:
            continue
        location = locate_quote(ref.quote, text)
        evidence = strong_evidence(ref.quote, text)
        rows.append(
            {
                "domain": ref.domain,
                "quote": ref.quote,
                "own_sentence": location.own if location is not None else None,
                "context": quote_context(ref.quote, text),
                "strong_marker": evidence.marker if evidence is not None else None,
            }
        )
    return {"text": check.text, "final_stance": check.stance.value, "support": rows}


def fact_rows(extraction: FactExtraction) -> list[dict[str, object]]:
    return [
        {
            "id": fact.id,
            "status": fact.status.value,
            "stance": fact.stance.value,
            "rebutted_by": fact.rebutted_by,
            "text": fact.text,
            "domains": sorted({ref.domain for ref in fact.support}),
        }
        for fact in extraction.fact_set.facts
    ]


def token_rows(calls: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "output_tokens": int(str(call["output_tokens"])),
            "share_of_limit": round(
                int(str(call["output_tokens"])) / FACT_EXTRACTION_MAX_TOKENS, 3
            ),
            "truncated": call["truncated"],
        }
        for call in calls
        if call["step"] == LLMStep.FACT_EXTRACTION.value
    ]


async def recorded_research(
    slug: str, planner: CapturingClient, service: ResearchService
) -> tuple[RecordedResearch, bool]:
    topic, fixed, _ = CASES[slug]
    recorded = read_text(RESEARCH_FILE.format(slug=slug))
    if recorded is not None:
        return RecordedResearch.model_validate_json(recorded), True
    queries = fixed or await plan_queries(planner, topic)
    research = await service.research(queries)
    fresh = RecordedResearch(
        topic=topic,
        queries=list(queries),
        planner_replies=[
            RecordedReply(schema_name=type(reply).__name__, reply=reply.model_dump(mode="json"))
            for reply in planner.replies
        ],
        research=research,
    )
    write_json(RESEARCH_FILE.format(slug=slug), fresh.model_dump(mode="json", by_alias=True))
    return fresh, False


@pytest.mark.parametrize("slug", list(CASES))
async def test_live_stance_after(slug: str, tmp_path: Path) -> None:
    settings = Settings()
    if settings.wikipedia_contact is None:
        pytest.skip("WIKIPEDIA_CONTACT is not set")
    limits = FactLimits.from_settings(settings)
    calls = LLMCallLog()
    llm_logger = logging.getLogger(LLM_LOGGER)
    llm_logger.setLevel(logging.INFO)
    llm_logger.addHandler(calls)
    try:
        async with (
            build_http_client(settings) as llm_http,
            build_research_http_client(settings) as research_http,
        ):
            factory = LLMClientFactory(settings, llm_http)
            planner = CapturingClient(factory.get_client(LLMStep.QUERY_PLANNING))
            service = ResearchService(
                build_sources(settings, research_http), ResearchLimits.from_settings(settings)
            )
            recorded, reused = await recorded_research(slug, planner, service)
            topic = recorded.topic
            research = recorded.research
            extractor = CapturingClient(factory.get_client(LLMStep.FACT_EXTRACTION))
            after = await extract_facts(extractor, topic, research.snippets, limits)
            extraction_calls = list(calls.calls)

            post: PostReady | None = None
            thread: PostReady | None = None
            if CASES[slug][2]:
                pipeline = Pipeline(
                    PipelineClients(
                        planner=DictReplayClient(planner_replies(recorded)),
                        extractor=ReplayClient(extractor.replies),
                        writer=factory.get_client(LLMStep.WRITING),
                        critic=factory.get_client(LLMStep.STYLE_CRITIQUE),
                    ),
                    RecordedResearcher(research),
                    InMemoryRunStore(settings.state_max_runs),
                    PipelineLimits.from_settings(settings).model_copy(
                        update={"examples_dir": tmp_path}
                    ),
                )
                outcome = await pipeline.run_topic(topic, None, StageTimer())
                post = outcome if isinstance(outcome, PostReady) else None
                if post is not None and PostAction.THREAD in post.actions:
                    reworked = await pipeline.rework(post.draft_id, PostAction.THREAD, StageTimer())
                    thread = reworked if isinstance(reworked, PostReady) else None
    finally:
        llm_logger.removeHandler(calls)

    snippets = shown_snippets(research.snippets, limits)
    reply = next(reply for reply in extractor.replies if isinstance(reply, ExtractedFacts))
    code_checks = verify_reply(reply.facts[:FACT_CANDIDATES_MAX], snippets, limits).checks
    texts = {snippet.id: snippet.text for snippet in snippets.values()}
    unknown = [
        str(candidate.stance)
        for candidate in reply.facts[:FACT_CANDIDATES_MAX]
        if candidate.stance is not None and parse_stance(candidate.stance) is None
    ]
    before_text = read_text(BEFORE_FILE.format(slug=slug))
    replayed: dict[str, object] | None = None
    if before_text is not None:
        before = RecordedBefore.model_validate_json(before_text)
        before_replay = await extract_facts(
            DictReplayClient(before.extractor_replies), topic, research.snippets, limits
        )
        replayed = {
            "stances": dict(Counter(fact.stance.value for fact in before_replay.fact_set.facts)),
            "stats": before_replay.stats.model_dump(),
        }
    report = {
        "topic": topic,
        "queries": recorded.queries,
        "research_reused": reused,
        "snippets": [
            {"alias": alias, "url": snippet.url, "chars": len(snippet.text)}
            for alias, snippet in snippets.items()
        ],
        "after": {
            "outcome": after.outcome.value,
            "stats": after.stats.model_dump(),
            "facts": fact_rows(after),
            "disputes": [dispute.model_dump() for dispute in after.fact_set.disputes],
            "attributed": stance_rows(after, snippets),
            "lowered_to_asserted": [
                check_row(check, texts) for check in code_checks if check.stance_unmarked
            ],
            "upgraded_by_code": [
                check_row(check, texts) for check in code_checks if check.stance_upgraded
            ],
            "role_swapped": [
                check_row(check, texts) for check in code_checks if check.stance_role_swapped
            ],
            "unknown_stance_values": unknown,
        },
        "before_replies_through_new_code": replayed,
        "extractor_replies": [
            {"schema": type(item).__name__, "reply": item.model_dump(mode="json")}
            for item in extractor.replies
        ],
        "extraction_tokens": token_rows(extraction_calls),
        "llm_calls_by_step": dict(Counter(str(call["step"]) for call in calls.calls)),
        "llm_calls_total": len(calls.calls),
        "post": summary(post) if post is not None else None,
        "thread": summary(thread) if thread is not None else None,
    }
    write_json(AFTER_FILE.format(slug=slug), report)

    assert after.fact_set.facts
    stances = {fact.stance for fact in after.fact_set.facts}
    assert stances <= set(ClaimStance)
