import logging
from collections import Counter
from collections.abc import Sequence
from typing import NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field

from app.config import style
from app.config.constants import (
    STYLE_DEFAULT_MIN_RETAINED_CHARS_RATIO,
    STYLE_DEFAULT_MIN_RETAINED_FACTS_RATIO,
    STYLE_REGRESSION_FREE_CHARS,
    STYLE_REGRESSION_FREE_FACTS,
)
from app.config.settings import Settings
from app.domain.draft import Draft, Revision
from app.domain.fact import FactSet
from app.domain.style import CriticStatus, StyleReport, StyleResult, StyleRule, Violation
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.prompts.style_critique import render_style_revision
from app.services.generator import WritingLimits, write_draft
from app.services.style_critic import CriticOutcome, critique_draft
from app.services.style_filter import check_draft

logger = logging.getLogger(__name__)

FORBIDDEN_PHRASE_RULES: frozenset[StyleRule] = frozenset(
    {
        StyleRule.BANNED_PHRASE,
        StyleRule.INVENTED_EXPERIENCE,
        StyleRule.CLICHE,
        StyleRule.TRIPLET,
        StyleRule.FILLER,
        StyleRule.OPINION,
        StyleRule.UNSUPPORTED_CLAIM,
    }
)
REPORT_ONLY_RULES: frozenset[StyleRule] = frozenset({StyleRule.LENGTH})
RULE_COUNT_SEPARATOR = ","
RULE_COUNT_TEMPLATE = "{rule}={count}"


class StyleLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    critic_enabled: bool
    max_regenerations: int = Field(ge=0)
    critic_max_findings: int = Field(ge=1)
    min_retained_chars_ratio: float = Field(
        default=STYLE_DEFAULT_MIN_RETAINED_CHARS_RATIO, gt=0, le=1
    )
    min_retained_facts_ratio: float = Field(
        default=STYLE_DEFAULT_MIN_RETAINED_FACTS_RATIO, gt=0, le=1
    )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            critic_enabled=settings.style_critic_enabled,
            max_regenerations=settings.style_max_regenerations,
            critic_max_findings=settings.style_critic_max_findings,
            min_retained_chars_ratio=settings.style_min_retained_chars_ratio,
            min_retained_facts_ratio=settings.style_min_retained_facts_ratio,
        )


class Evaluation(NamedTuple):
    draft: Draft
    report: StyleReport


def is_dangerous(violation: Violation) -> bool:
    return violation.rule.value in style.DANGEROUS_STYLE_RULES


def severity(report: StyleReport) -> tuple[int, int]:
    dangerous = sum(1 for violation in report.violations if is_dangerous(violation))
    return dangerous, len(report.violations)


def best_index(evaluations: Sequence[Evaluation]) -> int:
    return min(
        range(len(evaluations)),
        key=lambda index: (*severity(evaluations[index].report), -index),
    )


def text_chars(draft: Draft) -> int:
    return sum(len(text) for text in draft.texts)


def lost_too_much(previous: int, current: int, min_ratio: float, free: int) -> bool:
    return current < previous * min_ratio and previous - current > free


def is_regression(previous: Draft, regenerated: Draft, limits: StyleLimits) -> bool:
    return lost_too_much(
        text_chars(previous),
        text_chars(regenerated),
        limits.min_retained_chars_ratio,
        STYLE_REGRESSION_FREE_CHARS,
    ) or lost_too_much(
        len(previous.used_fact_ids),
        len(regenerated.used_fact_ids),
        limits.min_retained_facts_ratio,
        STYLE_REGRESSION_FREE_FACTS,
    )


def needs_regeneration(report: StyleReport) -> bool:
    return any(violation.rule not in REPORT_ONLY_RULES for violation in report.violations)


def forbidden_phrases(known: Sequence[str], violations: Sequence[Violation]) -> list[str]:
    phrases = list(known)
    for violation in violations:
        if (
            violation.rule in FORBIDDEN_PHRASE_RULES
            and violation.excerpt is not None
            and violation.excerpt not in phrases
        ):
            phrases.append(violation.excerpt)
    return phrases


async def run_critic(
    critic: LLMClient, draft: Draft, fact_set: FactSet, limits: StyleLimits
) -> tuple[CriticStatus, CriticOutcome]:
    if not limits.critic_enabled:
        return CriticStatus.DISABLED, CriticOutcome(violations=[])
    try:
        outcome = await critique_draft(critic, draft.texts, fact_set, limits.critic_max_findings)
    except LLMError as error:
        logger.warning("style critic failed, code checks kept: error=%s", type(error).__name__)
        return CriticStatus.FAILED, CriticOutcome(violations=[])
    return CriticStatus.CHECKED, outcome


async def evaluate(
    critic: LLMClient,
    draft: Draft,
    fact_set: FactSet,
    limits: StyleLimits,
    allow_closing_question: bool,
) -> Evaluation:
    code = check_draft(draft, allow_closing_question=allow_closing_question)
    status, outcome = await run_critic(critic, draft, fact_set, limits)
    return Evaluation(
        draft=draft,
        report=StyleReport(
            violations=[*code, *outcome.violations],
            critic=status,
            critic_dropped=outcome.dropped,
            critic_withdrawn=outcome.withdrawn,
            critic_over_limit=outcome.over_limit,
        ),
    )


def rule_counts(violations: Sequence[Violation]) -> str:
    counts = Counter(violation.rule.value for violation in violations)
    return RULE_COUNT_SEPARATOR.join(
        RULE_COUNT_TEMPLATE.format(rule=rule, count=count) for rule, count in sorted(counts.items())
    )


async def review_style(
    writer: LLMClient,
    critic: LLMClient,
    draft: Draft,
    fact_set: FactSet,
    writing_limits: WritingLimits,
    style_limits: StyleLimits,
    examples: Sequence[str] = (),
    *,
    angle: str | None = None,
    allow_closing_question: bool = False,
) -> StyleResult:
    evaluations = [await evaluate(critic, draft, fact_set, style_limits, allow_closing_question)]
    forbidden: list[str] = []
    regenerations = 0
    regeneration_failed = False
    regressions_rejected = 0
    after_regression = False
    while (
        needs_regeneration(evaluations[-1].report)
        and regenerations < style_limits.max_regenerations
    ):
        current = evaluations[-1]
        forbidden = forbidden_phrases(forbidden, current.report.violations)
        revision = Revision(
            instruction=render_style_revision(
                current.report.violations, forbidden, after_regression=after_regression
            ),
            previous=current.draft.texts,
        )
        try:
            regenerated = await write_draft(
                writer,
                fact_set,
                current.draft.post_format,
                writing_limits,
                examples,
                angle=angle,
                revision=revision,
            )
        except LLMError as error:
            logger.warning(
                "style regeneration failed, best draft kept: error=%s", type(error).__name__
            )
            regeneration_failed = True
            break
        regenerations += 1
        if is_regression(current.draft, regenerated, style_limits):
            regressions_rejected += 1
            after_regression = True
            continue
        after_regression = False
        evaluations.append(
            await evaluate(critic, regenerated, fact_set, style_limits, allow_closing_question)
        )
    chosen = best_index(evaluations)
    result = StyleResult(
        draft=evaluations[chosen].draft,
        report=evaluations[chosen].report,
        attempts=len(evaluations),
        chosen_attempt=chosen + 1,
        regenerations=regenerations,
        regeneration_failed=regeneration_failed,
        regressions_rejected=regressions_rejected,
    )
    logger.info(
        "style reviewed format=%s attempts=%d chosen=%d regenerations=%d "
        "regeneration_failed=%s regressions_rejected=%d critic=%s violations=%d dangerous=%d "
        "rules=%s critic_dropped=%d critic_withdrawn=%d critic_over_limit=%d",
        draft.post_format,
        result.attempts,
        result.chosen_attempt,
        regenerations,
        regeneration_failed,
        regressions_rejected,
        RULE_COUNT_SEPARATOR.join(evaluation.report.critic.value for evaluation in evaluations),
        len(result.report.violations),
        severity(result.report)[0],
        rule_counts(result.report.violations),
        sum(evaluation.report.critic_dropped for evaluation in evaluations),
        sum(evaluation.report.critic_withdrawn for evaluation in evaluations),
        sum(evaluation.report.critic_over_limit for evaluation in evaluations),
    )
    return result
