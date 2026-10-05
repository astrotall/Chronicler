import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config.constants import (
    CANDIDATE_ID_PREFIX,
    CONFIRMED_MIN_DOMAINS,
    DISPUTE_CHECK_MAX_TOKENS,
    DISPUTE_EXPLANATION_MAX_CHARS,
    DISPUTE_MIN_FACTS,
    FACT_CANDIDATES_MAX,
    FACT_EXTRACTION_MAX_TOKENS,
    FACT_ID_PREFIX,
    QUANTITY_DEFAULT_TOLERANCE,
    RELEVANCE_CHECK_MAX_TOKENS,
    RELEVANCE_MIN_FACTS,
    SNIPPET_ALIAS_PREFIX,
)
from app.config.settings import Settings
from app.domain.fact import (
    ClaimStance,
    Dispute,
    ExtractionStats,
    Fact,
    FactExtraction,
    FactSet,
    FactsExtracted,
    FactStatus,
    InsufficientFacts,
    SourceRef,
    all_weak,
)
from app.domain.snippet import Snippet
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.prompts.fact_extraction import render_dispute_check, render_fact_extraction
from app.prompts.fact_relevance import render_relevance_check
from app.services.fact_relevance import (
    FactRelevance,
    RelevanceCheck,
    RelevanceReport,
    cap_aspects,
    check_period,
    in_range,
    score_of,
)
from app.services.fact_selection import Selection, select_facts
from app.services.quantities import quantities_supported
from app.services.quote_check import QuoteCheck, check_quote, numbers_supported, text_segments
from app.services.source_domain import is_weak_source, source_domain
from app.services.stance import (
    has_stance_evidence,
    has_strong_evidence,
    parse_stance,
    reads_as_rebuttal,
)

logger = logging.getLogger(__name__)

type ReplyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
type Explanation = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=DISPUTE_EXPLANATION_MAX_CHARS
    ),
]

ALIAS_WRAPPING = "[] "


class ExtractedSupport(BaseModel):
    model_config = ConfigDict(frozen=True)

    snippet: ReplyText
    quote: ReplyText


class ExtractedRebuttal(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: ReplyText
    support: list[ExtractedSupport]


class ExtractedFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    stance: ReplyText | None = None
    text: ReplyText
    support: list[ExtractedSupport]
    rebuttal: ExtractedRebuttal | None = None


class ExtractedFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    facts: list[ExtractedFact]


class ReportedConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_ids: list[ReplyText]
    explanation: Explanation
    contradiction: bool


class ConflictReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    conflicts: list[ReportedConflict]


class SupportVerdict(StrEnum):
    VERIFIED = "verified"
    UNKNOWN_SNIPPET = "unknown_snippet"
    TOO_SHORT = "too_short"
    NOT_FOUND = "not_found"
    DUPLICATE = "duplicate"


class CandidateVerdict(StrEnum):
    VERIFIED = "verified"
    UNSUPPORTED = "unsupported"
    NUMBER_MISMATCH = "number_mismatch"
    UNKNOWN_STANCE = "unknown_stance"


QUOTE_VERDICTS: dict[QuoteCheck, SupportVerdict] = {
    QuoteCheck.MATCH: SupportVerdict.VERIFIED,
    QuoteCheck.TOO_SHORT: SupportVerdict.TOO_SHORT,
    QuoteCheck.NOT_FOUND: SupportVerdict.NOT_FOUND,
}


class FactLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_max_chars: int = Field(ge=1)
    min_quote_chars: int = Field(ge=1)
    max_facts: int = Field(ge=1)
    min_facts: int = Field(ge=1)
    domain_groups: tuple[tuple[str, ...], ...] = ()
    weak_domains: tuple[str, ...] = ()
    max_per_domain: int | None = Field(default=None, ge=1)
    domain_cap_floor: int = Field(default=0, ge=0)
    relevance: bool = False
    quantity_tolerance: float = Field(default=QUANTITY_DEFAULT_TOLERANCE, ge=0, lt=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            input_max_chars=settings.facts_input_max_chars,
            min_quote_chars=settings.facts_min_quote_chars,
            max_facts=settings.facts_max_facts,
            min_facts=settings.facts_min_facts,
            domain_groups=tuple(tuple(group) for group in settings.facts_domain_groups),
            weak_domains=tuple(settings.facts_weak_domains),
            max_per_domain=settings.facts_max_per_domain,
            domain_cap_floor=max(settings.facts_min_facts, settings.thread_min_facts),
            relevance=settings.facts_relevance_enabled,
            quantity_tolerance=settings.quantity_tolerance,
        )


class CandidateCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    support: tuple[SourceRef, ...]
    support_verdicts: tuple[SupportVerdict, ...]
    verdict: CandidateVerdict
    stance: ClaimStance = ClaimStance.ASSERTED
    stance_unmarked: bool = False
    stance_upgraded: bool = False
    stance_role_swapped: bool = False

    @property
    def attributed(self) -> bool:
        return self.stance is not ClaimStance.ASSERTED

    @property
    def status(self) -> FactStatus:
        if self.attributed:
            return FactStatus.SINGLE
        if len(self.independent_domains) >= CONFIRMED_MIN_DOMAINS:
            return FactStatus.CONFIRMED
        return FactStatus.SINGLE

    @property
    def independent_domains(self) -> set[str]:
        return {ref.domain for ref in self.support if not ref.weak}

    @property
    def lost_confirmed_by_weak(self) -> bool:
        all_domains = {ref.domain for ref in self.support}
        return (
            not self.attributed
            and len(all_domains) >= CONFIRMED_MIN_DOMAINS
            and len(self.independent_domains) < CONFIRMED_MIN_DOMAINS
        )

    @property
    def weak_only(self) -> bool:
        return all_weak(self.support)


class FoundDispute(BaseModel):
    model_config = ConfigDict(frozen=True)

    members: tuple[int, ...]
    explanation: str


class DisputeCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    disputes: tuple[FoundDispute, ...] = ()
    unknown_ids: int = 0
    withdrawn: int = 0


def share_cap(lengths: Sequence[int], budget: int) -> int | None:
    if sum(lengths) <= budget:
        return None
    remaining = budget
    ordered = sorted(lengths)
    for index, length in enumerate(ordered):
        share = remaining // (len(ordered) - index)
        if length > share:
            return share
        remaining -= length
    return None


def cut_at_word(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    if text[max_chars].isspace() or head[-1:].isspace():
        return head.rstrip()
    words = head.rsplit(maxsplit=1)
    return (words[0] if len(words) > 1 else head).rstrip()


def fit_to_budget(snippets: Sequence[Snippet], budget: int) -> tuple[list[Snippet], int]:
    cap = share_cap([len(snippet.text) for snippet in snippets], budget)
    if cap is None:
        return list(snippets), 0
    fitted: list[Snippet] = []
    truncated = 0
    for snippet in snippets:
        if len(snippet.text) <= cap:
            fitted.append(snippet)
            continue
        truncated += 1
        text = cut_at_word(snippet.text, cap)
        if text:
            fitted.append(snippet.model_copy(update={"text": text}))
    return fitted, truncated


def snippet_alias(position: int) -> str:
    return f"{SNIPPET_ALIAS_PREFIX}{position}"


def candidate_id(position: int) -> str:
    return f"{CANDIDATE_ID_PREFIX}{position}"


def fact_id(position: int) -> str:
    return f"{FACT_ID_PREFIX}{position}"


def normalize_label(label: str) -> str:
    return label.strip(ALIAS_WRAPPING).upper()


def verify_candidate(
    candidate: ExtractedFact | ExtractedRebuttal,
    snippets: Mapping[str, Snippet],
    limits: FactLimits,
) -> CandidateCheck:
    support: list[SourceRef] = []
    verdicts: list[SupportVerdict] = []
    seen: set[tuple[str, str]] = set()
    for item in candidate.support:
        alias = normalize_label(item.snippet)
        snippet = snippets.get(alias)
        if snippet is None:
            verdict = SupportVerdict.UNKNOWN_SNIPPET
        else:
            verdict = QUOTE_VERDICTS[check_quote(item.quote, snippet.text, limits.min_quote_chars)]
            key = (snippet.id, " ".join(text_segments(item.quote)))
            if verdict is SupportVerdict.VERIFIED and key in seen:
                verdict = SupportVerdict.DUPLICATE
            elif verdict is SupportVerdict.VERIFIED:
                seen.add(key)
                support.append(
                    SourceRef(
                        snippet_id=snippet.id,
                        url=snippet.url,
                        domain=source_domain(snippet.url, limits.domain_groups),
                        quote=item.quote,
                        weak=is_weak_source(snippet.url, limits.weak_domains),
                    )
                )
        if verdict not in (SupportVerdict.VERIFIED, SupportVerdict.DUPLICATE):
            logger.debug(
                "fact support dropped alias=%s snippet_id=%s reason=%s",
                alias if snippet is not None else None,
                snippet.id if snippet is not None else None,
                verdict,
            )
        verdicts.append(verdict)
    stance = parse_stance(candidate.stance) if isinstance(candidate, ExtractedFact) else None
    quotes = [ref.quote for ref in support]
    if not support:
        outcome = CandidateVerdict.UNSUPPORTED
    elif not numbers_supported(candidate.text, quotes) or not quantities_supported(
        candidate.text, quotes, limits.quantity_tolerance
    ):
        outcome = CandidateVerdict.NUMBER_MISMATCH
    elif isinstance(candidate, ExtractedFact) and stance is None:
        outcome = CandidateVerdict.UNKNOWN_STANCE
    else:
        outcome = CandidateVerdict.VERIFIED
    return CandidateCheck(
        text=candidate.text,
        support=tuple(support),
        support_verdicts=tuple(verdicts),
        verdict=outcome,
        stance=stance or ClaimStance.ASSERTED,
    )


def resolve_stance(
    claim: CandidateCheck, rebutted: bool, snippet_texts: Mapping[str, str]
) -> CandidateCheck:
    stance = ClaimStance.REBUTTED if rebutted else claim.stance
    if stance is ClaimStance.REBUTTED and reads_as_rebuttal(claim.support, snippet_texts):
        logger.debug("fact stance rebutted dropped, its quote opens as a rebuttal")
        claim = claim.model_copy(
            update={"stance": ClaimStance.ASSERTED, "stance_role_swapped": True}
        )
        stance = ClaimStance.ASSERTED
    if stance is ClaimStance.ASSERTED:
        if has_strong_evidence(claim.support, snippet_texts):
            logger.debug("fact stance raised to claimed, a strong marker is near its quotes")
            return claim.model_copy(update={"stance": ClaimStance.CLAIMED, "stance_upgraded": True})
        return claim
    if has_stance_evidence(stance, claim.support, snippet_texts):
        return claim.model_copy(update={"stance": stance})
    logger.debug("fact stance lowered to asserted, no attribution marker near its quotes")
    return claim.model_copy(update={"stance": ClaimStance.ASSERTED, "stance_unmarked": True})


class VerifiedFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    checks: tuple[CandidateCheck, ...]
    verified: tuple[CandidateCheck, ...]
    rebuttals: dict[int, tuple[int, ...]]
    rebuttals_proposed: int = 0


def verify_reply(
    candidates: Sequence[ExtractedFact], snippets: Mapping[str, Snippet], limits: FactLimits
) -> VerifiedFacts:
    snippet_texts = {snippet.id: snippet.text for snippet in snippets.values()}
    checks: list[CandidateCheck] = []
    verified: list[CandidateCheck] = []
    rebuttals: dict[int, tuple[int, ...]] = {}
    proposed = 0
    for candidate in candidates:
        claim = verify_candidate(candidate, snippets, limits)
        rebuttal = None
        if candidate.rebuttal is not None:
            proposed += 1
            rebuttal = verify_candidate(candidate.rebuttal, snippets, limits)
        rebutted = rebuttal is not None and rebuttal.verdict is CandidateVerdict.VERIFIED
        if claim.verdict is CandidateVerdict.VERIFIED:
            claim = resolve_stance(claim, rebutted, snippet_texts)
        if rebuttal is not None and rebutted:
            rebuttal = resolve_stance(rebuttal, False, snippet_texts)
        checks.append(claim)
        claim_index = len(verified) if claim.verdict is CandidateVerdict.VERIFIED else None
        if claim_index is not None:
            verified.append(claim)
        if rebuttal is None:
            continue
        checks.append(rebuttal)
        if not rebutted:
            continue
        if (
            claim_index is not None
            and claim.stance is ClaimStance.REBUTTED
            and rebuttal.stance is ClaimStance.ASSERTED
        ):
            rebuttals[claim_index] = (len(verified),)
        verified.append(rebuttal)
    return VerifiedFacts(
        checks=tuple(checks),
        verified=tuple(verified),
        rebuttals=rebuttals,
        rebuttals_proposed=proposed,
    )


def collect_disputes(report: ConflictReport, candidate_count: int) -> DisputeCheck:
    positions = {candidate_id(index + 1): index for index in range(candidate_count)}
    disputes: list[FoundDispute] = []
    unknown = 0
    withdrawn = 0
    for conflict in report.conflicts:
        if not conflict.contradiction:
            withdrawn += 1
            continue
        members: list[int] = []
        for reported in conflict.fact_ids:
            index = positions.get(normalize_label(reported))
            if index is None:
                unknown += 1
            elif index not in members:
                members.append(index)
        if len(members) >= DISPUTE_MIN_FACTS:
            disputes.append(FoundDispute(members=tuple(members), explanation=conflict.explanation))
    return DisputeCheck(disputes=tuple(disputes), unknown_ids=unknown, withdrawn=withdrawn)


async def find_disputes(client: LLMClient, candidates: Sequence[CandidateCheck]) -> DisputeCheck:
    pool = [
        index
        for index, candidate in enumerate(candidates)
        if candidate.stance is not ClaimStance.REBUTTED
    ]
    if len(pool) < DISPUTE_MIN_FACTS:
        return DisputeCheck()
    messages = render_dispute_check(
        [
            (candidate_id(position + 1), candidates[index].text, candidates[index].stance)
            for position, index in enumerate(pool)
        ]
    )
    report = await client.complete_json(
        messages, ConflictReport, max_tokens=DISPUTE_CHECK_MAX_TOKENS
    )
    found = collect_disputes(report, len(pool))
    return found.model_copy(
        update={
            "disputes": tuple(
                dispute.model_copy(
                    update={"members": tuple(pool[member] for member in dispute.members)}
                )
                for dispute in found.disputes
            )
        }
    )


def collect_relevance(report: RelevanceReport, candidate_count: int) -> RelevanceCheck:
    positions = {candidate_id(index + 1): index for index in range(candidate_count)}
    scores: dict[int, FactRelevance] = {}
    dropped = 0
    for entry in report.facts:
        index = positions.get(normalize_label(entry.id))
        if index is None or index in scores or not in_range(entry.relevance):
            dropped += 1
            continue
        scores[index] = score_of(entry)
    return RelevanceCheck(scores=cap_aspects(scores), dropped=dropped, failed=not scores)


async def find_relevance(client: LLMClient, topic: str, texts: Sequence[str]) -> RelevanceCheck:
    if len(texts) < RELEVANCE_MIN_FACTS:
        return RelevanceCheck()
    messages = render_relevance_check(
        topic, [(candidate_id(position + 1), text) for position, text in enumerate(texts)]
    )
    try:
        report = await client.complete_json(
            messages, RelevanceReport, max_tokens=RELEVANCE_CHECK_MAX_TOKENS
        )
    except LLMError as error:
        logger.warning(
            "fact relevance failed, the model order is kept error=%s", type(error).__name__
        )
        return RelevanceCheck(failed=True)
    check = check_period(collect_relevance(report, len(texts)), topic, texts)
    if check.failed:
        logger.warning("fact relevance returned no valid entry, the model order is kept")
    return check


def restored_rebuttals(
    attributed: Sequence[int],
    rebuttals: Mapping[int, Sequence[int]],
    taken: set[int],
) -> list[int]:
    restored: list[int] = []
    for index in attributed:
        for rebuttal in rebuttals.get(index, ()):
            if rebuttal not in taken and rebuttal not in restored:
                restored.append(rebuttal)
    return restored


def build_stats(
    *,
    snippets: int,
    snippets_truncated: int,
    candidates_over_limit: int,
    checks: Sequence[CandidateCheck],
    dispute_check: DisputeCheck,
    facts_disputed: int,
    selection: Selection,
    facts_kept: int,
    rebuttals_proposed: int = 0,
    rebuttals_verified: int = 0,
    rebuttals_restored: int = 0,
    relevance: RelevanceCheck | None = None,
) -> ExtractionStats:
    support = Counter(verdict for check in checks for verdict in check.support_verdicts)
    outcomes = Counter(check.verdict for check in checks)
    verified = [check for check in checks if check.verdict is CandidateVerdict.VERIFIED]
    ranking = relevance or RelevanceCheck()
    scores = ranking.scores.values()
    return ExtractionStats(
        snippets=snippets,
        snippets_truncated=snippets_truncated,
        candidates=len(checks) - rebuttals_proposed,
        candidates_over_limit=candidates_over_limit,
        support_proposed=support.total(),
        support_unknown_snippet=support[SupportVerdict.UNKNOWN_SNIPPET],
        support_too_short=support[SupportVerdict.TOO_SHORT],
        support_not_found=support[SupportVerdict.NOT_FOUND],
        support_number_mismatch=sum(
            len(check.support)
            for check in checks
            if check.verdict is CandidateVerdict.NUMBER_MISMATCH
        ),
        support_duplicate=support[SupportVerdict.DUPLICATE],
        facts_unsupported=outcomes[CandidateVerdict.UNSUPPORTED],
        facts_number_mismatch=outcomes[CandidateVerdict.NUMBER_MISMATCH],
        facts_verified=outcomes[CandidateVerdict.VERIFIED],
        disputes=len(dispute_check.disputes),
        disputes_withdrawn=dispute_check.withdrawn,
        dispute_unknown_ids=dispute_check.unknown_ids,
        facts_disputed=facts_disputed,
        facts_cut_by_limit=selection.cut_by_limit,
        support_weak=sum(ref.weak for check in verified for ref in check.support),
        facts_weak_only=sum(check.weak_only for check in verified),
        facts_lost_confirmed_by_weak=sum(check.lost_confirmed_by_weak for check in verified),
        facts_cut_by_domain_cap=selection.cut_by_domain_cap,
        facts_domain_cap_restored=selection.restored_by_floor,
        facts_claimed=sum(check.stance is ClaimStance.CLAIMED for check in verified),
        facts_rebutted=sum(check.stance is ClaimStance.REBUTTED for check in verified),
        facts_stance_unmarked=sum(check.stance_unmarked for check in verified),
        facts_stance_upgraded_by_code=sum(check.stance_upgraded for check in verified),
        facts_stance_role_swapped=sum(check.stance_role_swapped for check in verified),
        facts_unknown_stance=outcomes[CandidateVerdict.UNKNOWN_STANCE],
        rebuttals_proposed=rebuttals_proposed,
        rebuttals_verified=rebuttals_verified,
        rebuttals_restored=rebuttals_restored,
        relevance_scored=len(ranking.scores),
        relevance_dropped=ranking.dropped,
        relevance_failed=int(ranking.failed),
        relevance_period_overruled=ranking.period_overruled,
        facts_about_source=sum(score.about_source for score in scores),
        facts_outside_period=sum(score.outside_period for score in scores),
        facts_unrelated=sum(score.unrelated for score in scores),
        facts_set_aside_off_topic=selection.set_aside_off_topic,
        facts_off_topic_restored=selection.off_topic_restored,
        facts_kept=facts_kept,
    )


def log_stats(stats: ExtractionStats) -> None:
    logger.info(
        "fact extraction finished %s",
        " ".join(f"{name}={value}" for name, value in stats.model_dump().items()),
    )


async def extract_facts(
    client: LLMClient, topic: str, snippets: Sequence[Snippet], limits: FactLimits
) -> FactExtraction:
    if not topic.strip():
        raise ValueError("topic must not be blank")
    topic = topic.strip()
    shown, truncated = fit_to_budget(snippets, limits.input_max_chars)
    result = VerifiedFacts(checks=(), verified=(), rebuttals={})
    over_limit = 0
    if shown:
        by_alias = {snippet_alias(index + 1): snippet for index, snippet in enumerate(shown)}
        reply = await client.complete_json(
            render_fact_extraction(topic, list(by_alias.items()), limits.min_quote_chars),
            ExtractedFacts,
            max_tokens=FACT_EXTRACTION_MAX_TOKENS,
        )
        over_limit = max(len(reply.facts) - FACT_CANDIDATES_MAX, 0)
        result = verify_reply(reply.facts[:FACT_CANDIDATES_MAX], by_alias, limits)
    verified = list(result.verified)
    dispute_check = await find_disputes(client, verified)

    disputed = {index for dispute in dispute_check.disputes for index in dispute.members}
    open_indices = [index for index in range(len(verified)) if index not in disputed]
    attributed = [index for index in open_indices if verified[index].attributed]
    pool = [index for index in open_indices if not verified[index].attributed]
    relevance = (
        await find_relevance(client, topic, [verified[index].text for index in pool])
        if limits.relevance
        else RelevanceCheck()
    )
    selection = select_facts(
        verified,
        pool,
        max_facts=limits.max_facts,
        floor=max(limits.min_facts, limits.domain_cap_floor),
        max_per_domain=limits.max_per_domain,
        relevance={pool[position]: score for position, score in relevance.scores.items()},
    )
    kept = list(selection.kept)
    restored = restored_rebuttals(attributed, result.rebuttals, set(kept) | disputed)
    ordered = kept + restored + attributed + sorted(disputed)
    final_ids = {index: fact_id(position + 1) for position, index in enumerate(ordered)}
    facts = [
        Fact(
            id=final_ids[index],
            text=verified[index].text,
            support=list(verified[index].support),
            status=FactStatus.DISPUTED if index in disputed else verified[index].status,
            stance=verified[index].stance,
            rebutted_by=[
                final_ids[rebuttal]
                for rebuttal in result.rebuttals.get(index, ())
                if rebuttal in final_ids
            ],
        )
        for index in ordered
    ]
    fact_set = FactSet(
        topic=topic,
        facts=facts,
        disputes=[
            Dispute(
                fact_ids=[final_ids[index] for index in dispute.members],
                explanation=dispute.explanation,
            )
            for dispute in dispute_check.disputes
        ],
    )
    stats = build_stats(
        snippets=len(shown),
        snippets_truncated=truncated,
        candidates_over_limit=over_limit,
        checks=result.checks,
        dispute_check=dispute_check,
        facts_disputed=len(disputed),
        selection=selection,
        facts_kept=len(facts),
        rebuttals_proposed=result.rebuttals_proposed,
        rebuttals_verified=sum(len(links) for links in result.rebuttals.values()),
        rebuttals_restored=len(restored),
        relevance=relevance,
    )
    log_stats(stats)
    assertable_count = len(kept) + len(restored)
    if assertable_count < limits.min_facts:
        return InsufficientFacts(
            fact_set=fact_set,
            assertable_count=assertable_count,
            required=limits.min_facts,
            stats=stats,
        )
    return FactsExtracted(fact_set=fact_set, stats=stats)
