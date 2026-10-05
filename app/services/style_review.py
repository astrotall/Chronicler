import logging
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field

from app.config import style
from app.config.constants import (
    STYLE_DEFAULT_DROP_SURVIVING_CLAIMS,
    STYLE_DEFAULT_FRAGMENT_OVERLAP,
    STYLE_DEFAULT_MIN_RETAINED_CHARS_RATIO,
    STYLE_DEFAULT_MIN_RETAINED_FACTS_RATIO,
    STYLE_REGRESSION_FREE_CHARS,
    STYLE_REGRESSION_FREE_FACTS,
    THREAD_MIN_TWEETS,
)
from app.config.settings import Settings
from app.domain.draft import Draft, PostFormat, Revision, ThreadSize
from app.domain.fact import FactSet
from app.domain.style import (
    CriticStatus,
    StyleReport,
    StyleResult,
    StyleRule,
    Violation,
    ViolationSource,
)
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.prompts.style_critique import render_style_revision
from app.services.attribution_exemption import attribution_exempt
from app.services.claim_removal import RemovalGuard, remove_flagged_sentences
from app.services.claim_survival import (
    Survival,
    Survivor,
    exact_part,
    find_survivors,
    is_deletable,
    same_fragment,
)
from app.services.generator import (
    WritingLimits,
    build_parts,
    check_length,
    check_size,
    check_thread_size,
    facts_for_prompt,
    facts_to_verify,
    long_size,
    thread_size,
    unverified_numbers,
    write_draft,
)
from app.services.style_critic import CriticOutcome, critique_draft, locate_excerpt
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
CAUSE_SEPARATOR = "+"
CAUSE_TEMPLATE = "{kind}={previous}>{current}"
REASON_SEPARATOR = "|"
NO_REASON = "none"
UNFIXED_TEMPLATE = "{exact}+{near}"


class RegressionKind(StrEnum):
    THREAD_PARTS = "thread_parts"
    THREAD_FACTS = "thread_facts"
    CHARS = "chars"
    FACTS = "facts"


class Regression(NamedTuple):
    kind: RegressionKind
    previous: int
    current: int


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
    fragment_overlap: float = Field(default=STYLE_DEFAULT_FRAGMENT_OVERLAP, gt=0, le=1)
    drop_surviving_claims: bool = STYLE_DEFAULT_DROP_SURVIVING_CLAIMS

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            critic_enabled=settings.style_critic_enabled,
            max_regenerations=settings.style_max_regenerations,
            critic_max_findings=settings.style_critic_max_findings,
            min_retained_chars_ratio=settings.style_min_retained_chars_ratio,
            min_retained_facts_ratio=settings.style_min_retained_facts_ratio,
            fragment_overlap=settings.style_fragment_overlap,
            drop_surviving_claims=settings.style_drop_surviving_claims,
        )


class Evaluation(NamedTuple):
    draft: Draft
    report: StyleReport
    unfixed: tuple[Survivor, ...] = ()


class Demands(NamedTuple):
    problems: list[Violation]
    delete: frozenset[Violation]
    still_present: frozenset[Violation]
    attribution_kept: int


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


def fell_below_minimum(previous: int, current: int, minimum: int) -> bool:
    return current < minimum and current < previous


def regression_causes(
    previous: Draft, regenerated: Draft, limits: StyleLimits, size: ThreadSize
) -> list[Regression]:
    previous_parts, regenerated_parts = len(previous.parts), len(regenerated.parts)
    previous_facts = len(previous.used_fact_ids)
    regenerated_facts = len(regenerated.used_fact_ids)
    previous_chars, regenerated_chars = text_chars(previous), text_chars(regenerated)
    causes: list[Regression] = []
    if fell_below_minimum(previous_parts, regenerated_parts, size.min_tweets):
        causes.append(Regression(RegressionKind.THREAD_PARTS, previous_parts, regenerated_parts))
    if fell_below_minimum(previous_facts, regenerated_facts, size.min_facts):
        causes.append(Regression(RegressionKind.THREAD_FACTS, previous_facts, regenerated_facts))
    if lost_too_much(
        previous_chars,
        regenerated_chars,
        limits.min_retained_chars_ratio,
        STYLE_REGRESSION_FREE_CHARS,
    ):
        causes.append(Regression(RegressionKind.CHARS, previous_chars, regenerated_chars))
    if lost_too_much(
        previous_facts,
        regenerated_facts,
        limits.min_retained_facts_ratio,
        STYLE_REGRESSION_FREE_FACTS,
    ):
        causes.append(Regression(RegressionKind.FACTS, previous_facts, regenerated_facts))
    return causes


def describe_regressions(causes: Sequence[Regression]) -> str:
    return CAUSE_SEPARATOR.join(
        CAUSE_TEMPLATE.format(kind=cause.kind.value, previous=cause.previous, current=cause.current)
        for cause in causes
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


def verbatim_unfixed(evaluation: Evaluation) -> bool:
    return any(survivor.verdict is Survival.EXACT for survivor in evaluation.unfixed)


def needs_another_round(evaluation: Evaluation) -> bool:
    return needs_regeneration(evaluation.report) or verbatim_unfixed(evaluation)


def unfixed_entry(survivors: Sequence[Survivor]) -> str:
    exact = sum(1 for survivor in survivors if survivor.verdict is Survival.EXACT)
    return UNFIXED_TEMPLATE.format(exact=exact, near=len(survivors) - exact)


def build_demands(current: Evaluation, offered: FactSet, limits: StyleLimits) -> Demands:
    reported = current.report.violations
    exempt = attribution_exempt(reported, current.draft.texts, offered)
    problems = list(reported)
    still_present: set[Violation] = set()
    for survivor in current.unfixed:
        matches = [
            violation
            for violation in reported
            if is_deletable(violation)
            and violation not in exempt
            and same_fragment(
                violation.excerpt or "", survivor.violation.excerpt or "", limits.fragment_overlap
            )
        ]
        if matches:
            still_present.update(matches)
        else:
            problems.append(survivor.violation)
            still_present.add(survivor.violation)
    delete = frozenset(
        violation for violation in problems if is_deletable(violation) and violation not in exempt
    )
    return Demands(problems, delete, frozenset(still_present), len(exempt))


def removal_guard(
    post_format: PostFormat, size: ThreadSize, min_chars: int, limits: StyleLimits
) -> RemovalGuard:
    min_parts = max(THREAD_MIN_TWEETS, size.min_tweets) if post_format is PostFormat.THREAD else 1
    return RemovalGuard(
        min_parts=min_parts,
        min_chars=min_chars,
        keeps_enough=lambda previous, current: (
            not lost_too_much(
                previous, current, limits.min_retained_chars_ratio, STYLE_REGRESSION_FREE_CHARS
            )
        ),
    )


def removable_claims(evaluation: Evaluation, offered: FactSet) -> list[Violation]:
    reported = evaluation.report.violations
    exempt = attribution_exempt(reported, evaluation.draft.texts, offered)
    return [
        violation
        for violation in reported
        if violation.source is ViolationSource.CRITIC
        and is_deletable(violation)
        and violation not in exempt
    ]


def remaining_critic_violations(report: StyleReport, texts: Sequence[str]) -> list[Violation]:
    kept: list[Violation] = []
    for violation in report.violations:
        if violation.source is not ViolationSource.CRITIC:
            continue
        part = None if violation.excerpt is None else locate_excerpt(violation.excerpt, texts)
        if part is not None:
            kept.append(violation.model_copy(update={"part": part}))
    return kept


class Dropped(NamedTuple):
    evaluation: Evaluation
    blocked: int


def unreported_survivors(evaluation: Evaluation, limits: StyleLimits) -> int:
    texts = evaluation.draft.texts
    reported = [
        violation.excerpt or ""
        for violation in evaluation.report.violations
        if is_deletable(violation)
    ]
    return sum(
        1
        for survivor in evaluation.unfixed
        if survivor.verdict is Survival.EXACT
        and survivor.violation.excerpt is not None
        and exact_part(survivor.violation.excerpt, texts) is not None
        and not any(
            same_fragment(excerpt, survivor.violation.excerpt, limits.fragment_overlap)
            for excerpt in reported
        )
    )


def drop_flagged_claims(
    evaluation: Evaluation,
    fact_set: FactSet,
    offered: FactSet,
    writing_limits: WritingLimits,
    size: ThreadSize,
    limits: StyleLimits,
    allow_closing_question: bool,
) -> Dropped:
    draft = evaluation.draft
    if not limits.drop_surviving_claims or draft.post_format is PostFormat.SHORT:
        return Dropped(evaluation, 0)
    excerpts = [
        violation.excerpt
        for violation in removable_claims(evaluation, offered)
        if violation.excerpt is not None
    ]
    min_chars = long_size(draft.post_format, writing_limits, offered).min_chars
    removal = remove_flagged_sentences(
        draft.texts, excerpts, removal_guard(draft.post_format, size, min_chars, limits)
    )
    if not removal.removed:
        return Dropped(evaluation, removal.blocked)
    parts = build_parts(removal.texts, draft.post_format, writing_limits)
    used = draft.used_fact_ids
    cut = draft.model_copy(
        update={
            "parts": parts,
            "length_violations": [
                *check_length(parts, draft.post_format, writing_limits),
                *check_size(parts, used, long_size(draft.post_format, writing_limits, offered)),
                *check_thread_size(parts, used, size),
            ],
            "unverified_numbers": unverified_numbers(
                removal.texts, facts_to_verify(fact_set, offered, draft.post_format)
            ),
            "removed_fragments": removal.removed,
        }
    )
    report = evaluation.report
    return Dropped(
        Evaluation(
            draft=cut,
            report=report.model_copy(
                update={
                    "violations": [
                        *check_draft(cut, allow_closing_question=allow_closing_question),
                        *remaining_critic_violations(report, removal.texts),
                    ]
                }
            ),
            unfixed=evaluation.unfixed,
        ),
        removal.blocked,
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
    offered = facts_for_prompt(fact_set, draft.post_format, writing_limits)
    size = thread_size(draft.post_format, writing_limits, offered)
    evaluations = [await evaluate(critic, draft, fact_set, style_limits, allow_closing_question)]
    forbidden: list[str] = []
    regenerations = 0
    regeneration_failed = False
    regressions_rejected = 0
    after_regression = False
    triggers: list[str] = []
    rejected: list[str] = []
    unfixed_entries: list[str] = []
    unfixed_totals: list[int] = []
    attribution_kept = 0
    while needs_another_round(evaluations[-1]) and regenerations < style_limits.max_regenerations:
        current = evaluations[-1]
        demands = build_demands(current, offered, style_limits)
        attribution_kept += demands.attribution_kept
        forbidden = forbidden_phrases(forbidden, demands.problems)
        triggers.append(rule_counts(demands.problems) or NO_REASON)
        revision = Revision(
            instruction=render_style_revision(
                demands.problems,
                forbidden,
                after_regression=after_regression,
                delete=demands.delete,
                still_present=demands.still_present,
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
        causes = regression_causes(current.draft, regenerated, style_limits, size)
        if causes:
            rejected.append(describe_regressions(causes))
            regressions_rejected += 1
            after_regression = True
            continue
        after_regression = False
        survivors = find_survivors(
            [violation for violation in demands.problems if violation in demands.delete],
            regenerated.texts,
            style_limits.fragment_overlap,
        )
        unfixed_entries.append(unfixed_entry(survivors))
        unfixed_totals.append(len(survivors))
        evaluated = await evaluate(
            critic, regenerated, fact_set, style_limits, allow_closing_question
        )
        evaluations.append(evaluated._replace(unfixed=tuple(survivors)))
    chosen = best_index(evaluations)
    dropped = drop_flagged_claims(
        evaluations[chosen],
        fact_set,
        offered,
        writing_limits,
        size,
        style_limits,
        allow_closing_question,
    )
    final = dropped.evaluation
    unreported = unreported_survivors(final, style_limits)
    result = StyleResult(
        draft=final.draft,
        report=final.report,
        attempts=len(evaluations),
        chosen_attempt=chosen + 1,
        regenerations=regenerations,
        regeneration_failed=regeneration_failed,
        regressions_rejected=regressions_rejected,
        unfixed_per_round=unfixed_totals,
        attribution_kept=attribution_kept,
        unreported_survivors=unreported,
        removal_blocked=dropped.blocked,
    )
    logger.info(
        "style reviewed format=%s attempts=%d chosen=%d regenerations=%d "
        "regeneration_failed=%s regressions_rejected=%d critic=%s violations=%d dangerous=%d "
        "rules=%s critic_dropped=%d critic_withdrawn=%d critic_over_limit=%d "
        "regeneration_triggers=%s regression_causes=%s unfixed_per_round=%s "
        "attribution_kept=%d removed_fragments=%d removal_blocked=%d unreported_survivors=%d",
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
        REASON_SEPARATOR.join(triggers) or NO_REASON,
        REASON_SEPARATOR.join(rejected) or NO_REASON,
        REASON_SEPARATOR.join(unfixed_entries) or NO_REASON,
        attribution_kept,
        len(result.draft.removed_fragments),
        dropped.blocked,
        unreported,
    )
    return result
