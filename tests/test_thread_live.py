import json
import logging
from pathlib import Path
from typing import NamedTuple

import pytest
from app.config.constants import LLMStep
from app.config.settings import Settings
from app.domain.draft import Draft, LengthIssue, PostFormat
from app.domain.fact import FactSet
from app.domain.style import StyleResult
from app.llm.factory import LLMClientFactory, build_http_client
from app.prompts.writing import assertable_facts
from app.services.generator import WritingLimits, facts_for_prompt, thread_size, write_draft
from app.services.style_review import StyleLimits, review_style

from fact_snapshot import load_fact_set
from live_keys import LiveKeys, skip_without_key
from llm_helpers import RecordingLLMClient, as_client
from test_generator_live import live_settings

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
OUTPUT_TEMPLATE = "his42_{slug}.json"
SAMPLES_PER_SET = 3
NO_EXAMPLES: tuple[str, ...] = ()
FIRST_TWEET_PHRASE_MAX_CHARS = 160
LOGGED_SOURCES = frozenset({"app.services.generator", "app.services.style_review"})

logger = logging.getLogger(__name__)


class ThreadCase(NamedTuple):
    slug: str
    snapshot: Path


THREAD_CASES = [
    ThreadCase(slug="kulikovo", snapshot=OUTPUT_DIR / "his8_kulikovo.json"),
    ThreadCase(slug="soviet_day_1930s", snapshot=OUTPUT_DIR / "his8_soviet_day_1930s.json"),
]


class Sample(NamedTuple):
    first: Draft
    result: StyleResult
    writer_calls: int
    critic_calls: int
    first_draft_log: list[str]
    style_log: list[str]


def first_phrase(text: str, limit: int) -> str:
    return text.strip().split("\n", 1)[0][:limit]


def facts_per_tweet(draft: Draft) -> float:
    return round(len(draft.used_fact_ids) / len(draft.parts), 2)


def sample_report(
    index: int, sample: Sample, offered: FactSet, ceiling: int, include_text: bool
) -> dict[str, object]:
    chosen = sample.result.draft
    assertable_ids = {fact.id for fact in assertable_facts(offered)}
    attributed = [fact for fact in offered.facts if fact.id not in assertable_ids]
    report: dict[str, object] = {
        "sample": index,
        "offered_facts": len(offered.facts),
        "offered_assertable": len(assertable_ids),
        "attributed_offered": [
            {
                "id": fact.id,
                "stance": fact.stance.value,
                "status": fact.status.value,
                "text": fact.text,
                "used": fact.id in chosen.used_fact_ids,
            }
            for fact in attributed
        ],
        "tweets": len(chosen.parts),
        "used_facts": len(chosen.used_fact_ids),
        "facts_per_tweet": facts_per_tweet(chosen),
        "first_draft_tweets": len(sample.first.parts),
        "first_draft_used_facts": len(sample.first.used_fact_ids),
        "length_regenerations_first_draft": sample.first.attempts - 1,
        "style_regenerations": sample.result.regenerations,
        "regressions_rejected": sample.result.regressions_rejected,
        "chosen_attempt": sample.result.chosen_attempt,
        "regeneration_failed": sample.result.regeneration_failed,
        "length_violations": [violation.issue.value for violation in chosen.length_violations],
        "remaining_violations": [
            {"rule": violation.rule.value, "excerpt": violation.excerpt}
            for violation in sample.result.report.violations
        ],
        "unverified_numbers": chosen.unverified_numbers,
        "first_tweet_first_phrase": first_phrase(chosen.texts[0], FIRST_TWEET_PHRASE_MAX_CHARS),
        "tweet_lengths": [len(text) for text in chosen.texts],
        "at_ceiling": len(chosen.parts) == ceiling,
        "first_draft_log": sample.first_draft_log,
        "style_log": sample.style_log,
        "writer_calls": sample.writer_calls,
        "critic_calls": sample.critic_calls,
    }
    if include_text:
        report["texts"] = chosen.texts
        report["first_draft_texts"] = sample.first.texts
    return report


def drafting_lines(records: list[logging.LogRecord]) -> list[str]:
    return [
        record.getMessage()
        for record in records
        if record.name in LOGGED_SOURCES and record.levelno >= logging.INFO
    ]


def save_report(slug: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / OUTPUT_TEMPLATE.format(slug=slug)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check_sample(sample: Sample, offered: FactSet, limits: WritingLimits) -> None:
    draft = sample.result.draft
    size = thread_size(PostFormat.THREAD, limits, offered)
    known = {fact.id for fact in offered.facts}
    assert set(draft.used_fact_ids) <= known
    flagged = {violation.issue for violation in draft.length_violations}
    assert len(draft.parts) <= limits.thread_max_tweets or LengthIssue.TOO_MANY_PARTS in flagged
    assert len(draft.parts) >= size.min_tweets or LengthIssue.TOO_FEW_PARTS in flagged
    assert len(draft.used_fact_ids) >= size.min_facts or LengthIssue.TOO_FEW_FACTS in flagged


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
@pytest.mark.parametrize("case", THREAD_CASES, ids=[case.slug for case in THREAD_CASES])
async def test_live_thread_keeps_its_minimum(
    case: ThreadCase, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    fact_set = load_fact_set(case.snapshot)
    settings: Settings = live_settings()
    writing_limits = WritingLimits.from_settings(settings)
    offered = facts_for_prompt(fact_set, PostFormat.THREAD, writing_limits)
    style_limits = StyleLimits.from_settings(settings)
    samples: list[Sample] = []
    async with build_http_client(settings) as http_client:
        factory = LLMClientFactory(settings, http_client)
        writer = RecordingLLMClient(factory.get_client(LLMStep.WRITING))
        critic = RecordingLLMClient(factory.get_client(LLMStep.STYLE_CRITIQUE))
        for _ in range(SAMPLES_PER_SET):
            writer_before, critic_before = len(writer.prompts), len(critic.prompts)
            log_start = len(caplog.records)
            first = await write_draft(
                as_client(writer), fact_set, PostFormat.THREAD, writing_limits, NO_EXAMPLES
            )
            first_draft_log = drafting_lines(caplog.records[log_start:])
            style_start = len(caplog.records)
            result = await review_style(
                as_client(writer),
                as_client(critic),
                first,
                fact_set,
                writing_limits,
                style_limits,
                NO_EXAMPLES,
            )
            samples.append(
                Sample(
                    first,
                    result,
                    len(writer.prompts) - writer_before,
                    len(critic.prompts) - critic_before,
                    first_draft_log,
                    drafting_lines(caplog.records[style_start:]),
                )
            )
    requests = len(writer.prompts) + len(critic.prompts)
    logger.info("live requests=%d", requests)

    report: dict[str, object] = {
        "topic": fact_set.topic,
        "format": PostFormat.THREAD.value,
        "facts_total": len(fact_set.facts),
        "offered_facts": len(offered.facts),
        "offered_ids": [fact.id for fact in offered.facts],
        "thread_max_facts": writing_limits.thread_max_facts,
        "thread_max_attributed": writing_limits.thread_max_attributed,
        "thread_min_tweets": writing_limits.thread_min_tweets,
        "thread_min_used_facts": writing_limits.thread_min_used_facts,
        "effective_size": thread_size(PostFormat.THREAD, writing_limits, offered).model_dump(),
        "requests_total": requests,
        "requests_writer": len(writer.prompts),
        "requests_critic": len(critic.prompts),
        "samples": [
            sample_report(
                index, sample, offered, writing_limits.thread_max_tweets, include_text=True
            )
            for index, sample in enumerate(samples, start=1)
        ],
    }
    save_report(case.slug, report)
    for sample in samples:
        check_sample(sample, offered, writing_limits)
