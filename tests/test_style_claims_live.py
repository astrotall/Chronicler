import json
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from app.config.constants import LLMStep
from app.domain.draft import Draft, PostFormat, Revision
from app.domain.fact import FactSet
from app.domain.style import StyleResult, Violation
from app.llm.client import LLMClient
from app.llm.factory import LLMClientFactory, build_http_client
from app.services import style_review
from app.services.claim_survival import find_survivors, is_deletable, score_fragment
from app.services.generator import WritingLimits, write_draft
from app.services.style_critic import CriticOutcome, critique_draft
from app.services.style_review import StyleLimits, review_style

from fact_snapshot import load_fact_set
from live_keys import LiveKeys, skip_without_key
from llm_helpers import RecordingLLMClient, as_client
from test_generator_live import live_settings

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
OUTPUT_TEMPLATE = "his41_{label}_{slug}.json"
LABEL_ENV = "HIS41_LABEL"
DROP_ENV = "HIS41_DROP"
DEFAULT_LABEL = "run"
SAMPLES_PER_SET = 3
SCORE_DIGITS = 3
NO_EXAMPLES: tuple[str, ...] = ()
LOGGED_SOURCES = frozenset({"app.services.generator", "app.services.style_review"})

type Critique = Callable[[LLMClient, Sequence[str], FactSet, int], object]


class Case(NamedTuple):
    slug: str
    snapshot: Path


CASES = [
    Case(slug="kulikovo", snapshot=OUTPUT_DIR / "his8_kulikovo.json"),
    Case(slug="soviet_day_1930s", snapshot=OUTPUT_DIR / "his8_soviet_day_1930s.json"),
]


class RoundLog:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self.events: list[dict[str, object]] = []
        self.flagged: list[Violation] = []


def violation_entry(violation: Violation) -> dict[str, object]:
    return {
        "rule": violation.rule.value,
        "source": violation.source.value,
        "part": violation.part,
        "excerpt": violation.excerpt,
        "explanation": violation.explanation,
    }


def calibration_entries(
    flagged: Sequence[Violation], texts: Sequence[str], threshold: float
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for violation in flagged:
        if violation.excerpt is None:
            continue
        scored = score_fragment(violation.excerpt, texts, threshold)
        entries.append(
            {
                "rule": violation.rule.value,
                "stems": scored.stems,
                "score": round(scored.score, SCORE_DIGITS),
                "verdict": scored.verdict.value,
            }
        )
    return entries


def recording_critique(log: RoundLog) -> Critique:
    async def critique(
        client: LLMClient, texts: Sequence[str], fact_set: FactSet, max_findings: int
    ) -> CriticOutcome:
        outcome = await critique_draft(client, texts, fact_set, max_findings)
        log.flagged = [violation for violation in outcome.violations if is_deletable(violation)]
        log.events.append(
            {
                "kind": "critic",
                "texts": list(texts),
                "violations": [violation_entry(violation) for violation in outcome.violations],
                "dropped": outcome.dropped,
                "withdrawn": outcome.withdrawn,
                "over_limit": outcome.over_limit,
            }
        )
        return outcome

    return critique


def recording_writer(log: RoundLog) -> Callable[..., object]:
    async def write(
        client: LLMClient,
        fact_set: FactSet,
        post_format: PostFormat,
        limits: WritingLimits,
        examples: Sequence[str] = (),
        *,
        angle: str | None = None,
        revision: Revision | None = None,
    ) -> Draft:
        draft = await write_draft(
            client, fact_set, post_format, limits, examples, angle=angle, revision=revision
        )
        log.events.append(
            {
                "kind": "regeneration",
                "instruction": None if revision is None else revision.instruction,
                "texts": draft.texts,
                "used_fact_ids": draft.used_fact_ids,
                "attempts": draft.attempts,
                "flagged": len(log.flagged),
                "survivors": len(find_survivors(log.flagged, draft.texts, log.threshold)),
                "calibration": calibration_entries(log.flagged, draft.texts, log.threshold),
            }
        )
        return draft

    return write


def drafting_lines(records: list[logging.LogRecord]) -> list[str]:
    return [
        record.getMessage()
        for record in records
        if record.name in LOGGED_SOURCES and record.levelno >= logging.INFO
    ]


def result_entry(result: StyleResult) -> dict[str, object]:
    return {
        "texts": result.draft.texts,
        "used_fact_ids": result.draft.used_fact_ids,
        "chosen_attempt": result.chosen_attempt,
        "attempts": result.attempts,
        "regenerations": result.regenerations,
        "regressions_rejected": result.regressions_rejected,
        "regeneration_failed": result.regeneration_failed,
        "length_violations": [issue.issue.value for issue in result.draft.length_violations],
        "violations": [violation_entry(violation) for violation in result.report.violations],
        "dropped_tail": result.draft.dropped_tail,
        "removed_fragments": result.draft.removed_fragments,
        "unfixed_per_round": result.unfixed_per_round,
        "attribution_kept": result.attribution_kept,
        "unreported_survivors": result.unreported_survivors,
        "removal_blocked": result.removal_blocked,
    }


def save_report(label: str, slug: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / OUTPUT_TEMPLATE.format(label=label, slug=slug)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
@pytest.mark.parametrize("case", CASES, ids=[case.slug for case in CASES])
async def test_live_flagged_claims_in_threads(
    case: Case, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    label = os.environ.get(LABEL_ENV, DEFAULT_LABEL)
    drop = os.environ.get(DROP_ENV) == "1"
    fact_set = load_fact_set(case.snapshot)
    settings = live_settings()
    writing_limits = WritingLimits.from_settings(settings)
    style_limits = StyleLimits.from_settings(settings)
    style_limits = style_limits.model_copy(update={"drop_surviving_claims": drop})
    samples: list[dict[str, object]] = []
    async with build_http_client(settings) as http_client:
        factory = LLMClientFactory(settings, http_client)
        writer = RecordingLLMClient(factory.get_client(LLMStep.WRITING))
        critic = RecordingLLMClient(factory.get_client(LLMStep.STYLE_CRITIQUE))
        for index in range(1, SAMPLES_PER_SET + 1):
            writer_before, critic_before = len(writer.prompts), len(critic.prompts)
            first = await write_draft(
                as_client(writer), fact_set, PostFormat.THREAD, writing_limits, NO_EXAMPLES
            )
            log = RoundLog(style_limits.fragment_overlap)
            monkeypatch.setattr(style_review, "critique_draft", recording_critique(log))
            monkeypatch.setattr(style_review, "write_draft", recording_writer(log))
            log_start = len(caplog.records)
            result = await review_style(
                as_client(writer),
                as_client(critic),
                first,
                fact_set,
                writing_limits,
                style_limits,
                NO_EXAMPLES,
            )
            monkeypatch.undo()
            samples.append(
                {
                    "sample": index,
                    "first_draft_texts": first.texts,
                    "first_draft_used_fact_ids": first.used_fact_ids,
                    "events": log.events,
                    "result": result_entry(result),
                    "style_log": drafting_lines(caplog.records[log_start:]),
                    "writer_calls": len(writer.prompts) - writer_before,
                    "critic_calls": len(critic.prompts) - critic_before,
                }
            )
    save_report(
        label,
        case.slug,
        {
            "label": label,
            "topic": fact_set.topic,
            "drop_surviving_claims": drop,
            "fragment_overlap": style_limits.fragment_overlap,
            "requests_writer": len(writer.prompts),
            "requests_critic": len(critic.prompts),
            "samples": samples,
        },
    )
