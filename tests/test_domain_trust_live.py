import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.config.constants import LLMStep
from app.config.settings import Settings
from app.domain.draft import PostFormat
from app.domain.fact import Fact, FactExtraction, FactSet, all_weak
from app.domain.llm import LLMResult, Message
from app.domain.pipeline import PostAction, PostReady
from app.domain.research import ResearchResult
from app.domain.snippet import Snippet
from app.llm.factory import LLMClientFactory, build_http_client
from app.research.factory import build_sources
from app.research.http import build_research_http_client
from app.services.fact_selection import cap_domains
from app.services.facts import FactLimits, extract_facts
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.query_planning import plan_queries
from app.services.research import ResearchLimits, ResearchService
from app.services.run_store import InMemoryRunStore
from app.services.source_domain import is_weak_source, source_domain
from pydantic import BaseModel

from live_keys import LiveKeys, skip_without_key
from test_pipeline_live import (
    LLM_LOGGER,
    TOPICS,
    CapturingClient,
    CountingSource,
    LLMCallLog,
    StageTimer,
    summary,
)

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
REPORT_TEMPLATE = "his28_{slug}.json"

pytestmark = [
    pytest.mark.integration,
    skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key),
    skip_without_key("TAVILY_API_KEY", KEYS.tavily_api_key),
]


class ReplayClient:
    def __init__(self, replies: Sequence[BaseModel]) -> None:
        self._replies = list(replies)
        self.served = 0

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
            if isinstance(reply, schema):
                self.served += 1
                return reply
        raise AssertionError(f"no recorded reply for {schema.__name__}")


class RecordedResearcher:
    def __init__(self, result: ResearchResult) -> None:
        self._result = result

    @property
    def source_names(self) -> list[str]:
        return ["recorded"]

    async def research(self, queries: Sequence[str]) -> ResearchResult:
        return self._result


def judged(fact: Fact, weak_domains: Sequence[str]) -> Fact:
    refs = [
        ref.model_copy(update={"weak": is_weak_source(ref.url, weak_domains)})
        for ref in fact.support
    ]
    return fact.model_copy(update={"support": refs})


def fact_row(fact: Fact, weak_domains: Sequence[str]) -> dict[str, object]:
    view = judged(fact, weak_domains)
    return {
        "id": fact.id,
        "text": fact.text,
        "status": fact.status.value,
        "home_domain": cap_domains(view.support)[0],
        "weak_only": all_weak(view.support),
        "support": [
            {"domain": ref.domain, "host": ref.url.split("/")[2], "weak": ref.weak}
            for ref in view.support
        ],
    }


def block(
    extraction: FactExtraction, weak_domains: Sequence[str], limits: FactLimits
) -> dict[str, object]:
    rows = [fact_row(fact, weak_domains) for fact in extraction.fact_set.facts]
    return {
        "outcome": extraction.outcome.value,
        "limits": limits.model_dump(mode="json"),
        "stats": extraction.stats.model_dump(),
        "facts": rows,
        "count": len(rows),
        "confirmed": sum(row["status"] == "confirmed" for row in rows),
        "weak_only": sum(bool(row["weak_only"]) for row in rows),
        "weak_only_share": round(
            sum(bool(row["weak_only"]) for row in rows) / len(rows) if rows else 0.0, 3
        ),
        "per_home_domain": dict(Counter(str(row["home_domain"]) for row in rows).most_common()),
    }


def differing(before: FactSet, after: FactSet, weak_domains: Sequence[str]) -> dict[str, object]:
    before_texts = {fact.text for fact in before.facts}
    after_texts = {fact.text for fact in after.facts}
    return {
        "displaced": [
            fact_row(fact, weak_domains) for fact in before.facts if fact.text not in after_texts
        ],
        "added": [
            fact_row(fact, weak_domains) for fact in after.facts if fact.text not in before_texts
        ],
    }


def snippet_domains(snippets: Sequence[Snippet], limits: FactLimits) -> dict[str, object]:
    domains = Counter(source_domain(snippet.url, limits.domain_groups) for snippet in snippets)
    weak = Counter(
        source_domain(snippet.url, limits.domain_groups)
        for snippet in snippets
        if is_weak_source(snippet.url, limits.weak_domains)
    )
    return {"snippets_by_domain": dict(domains.most_common()), "weak_snippets": dict(weak)}


def save_report(slug: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / REPORT_TEMPLATE.format(slug=slug)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize("slug", list(TOPICS))
async def test_live_domain_trust_before_and_after(slug: str) -> None:
    settings = Settings()
    if settings.wikipedia_contact is None:
        pytest.skip("WIKIPEDIA_CONTACT is not set")
    topic = TOPICS[slug]
    after_limits = FactLimits.from_settings(settings)
    before_limits = after_limits.model_copy(update={"weak_domains": (), "max_per_domain": None})
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
            extractor = CapturingClient(factory.get_client(LLMStep.FACT_EXTRACTION))
            sources = [CountingSource(source) for source in build_sources(settings, research_http)]
            service = ResearchService(sources, ResearchLimits.from_settings(settings))

            queries = await plan_queries(planner, topic)
            research = await service.research(queries)
            before = await extract_facts(extractor, topic, research.snippets, before_limits)
            calls_for_facts = len(calls.calls)

            replay_extractor = ReplayClient(extractor.replies)
            after = await extract_facts(replay_extractor, topic, research.snippets, after_limits)
            assert replay_extractor.served == len(extractor.replies)

            pipeline = Pipeline(
                PipelineClients(
                    planner=ReplayClient(planner.replies),
                    extractor=ReplayClient(extractor.replies),
                    writer=factory.get_client(LLMStep.WRITING),
                    critic=factory.get_client(LLMStep.STYLE_CRITIQUE),
                ),
                RecordedResearcher(research),
                InMemoryRunStore(settings.state_max_runs),
                PipelineLimits.from_settings(settings),
            )
            outcome = await pipeline.run_topic(topic, None, StageTimer())
            thread = (
                await pipeline.rework(outcome.draft_id, PostAction.THREAD, StageTimer())
                if isinstance(outcome, PostReady) and PostAction.THREAD in outcome.actions
                else None
            )
    finally:
        llm_logger.removeHandler(calls)

    weak_domains = after_limits.weak_domains
    report = {
        "topic": topic,
        "queries": queries,
        "search_calls": {source.name: source.calls for source in sources},
        "snippets": len(research.snippets),
        **snippet_domains(research.snippets, after_limits),
        "failures": [failure.model_dump() for failure in research.failures],
        "before": block(before, weak_domains, before_limits),
        "after": block(after, weak_domains, after_limits),
        "diff": differing(before.fact_set, after.fact_set, weak_domains),
        "llm_calls_planning_extraction": calls_for_facts,
        "llm_calls_total": len(calls.calls),
        "llm_calls_by_step": dict(Counter(str(call["step"]) for call in calls.calls)),
        "pipeline_outcome": type(outcome).__name__,
        "post": summary(outcome) if isinstance(outcome, PostReady) else None,
        "thread_offered": thread is not None,
        "thread": summary(thread) if isinstance(thread, PostReady) else type(thread).__name__,
    }
    save_report(slug, report)

    assert before.fact_set.facts
    if isinstance(outcome, PostReady):
        assert outcome.result.draft.post_format is PostFormat.SHORT
