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
    SNIPPET_ALIAS_PREFIX,
)
from app.config.settings import Settings
from app.domain.fact import (
    Dispute,
    ExtractionStats,
    Fact,
    FactExtraction,
    FactSet,
    FactsExtracted,
    FactStatus,
    InsufficientFacts,
    SourceRef,
)
from app.domain.snippet import Snippet
from app.llm.client import LLMClient
from app.prompts.fact_extraction import render_dispute_check, render_fact_extraction
from app.services.quote_check import QuoteCheck, check_quote, numbers_supported, text_segments
from app.services.source_domain import source_domain

logger = logging.getLogger(__name__)

type ReplyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
type Explanation = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=DISPUTE_EXPLANATION_MAX_CHARS
    ),
]

ALIAS_WRAPPING = "[] "
STATUS_PRIORITY: dict[FactStatus, int] = {FactStatus.CONFIRMED: 0, FactStatus.SINGLE: 1}


class ExtractedSupport(BaseModel):
    model_config = ConfigDict(frozen=True)

    snippet: ReplyText
    quote: ReplyText


class ExtractedFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: ReplyText
    support: list[ExtractedSupport]


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

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            input_max_chars=settings.facts_input_max_chars,
            min_quote_chars=settings.facts_min_quote_chars,
            max_facts=settings.facts_max_facts,
            min_facts=settings.facts_min_facts,
            domain_groups=tuple(tuple(group) for group in settings.facts_domain_groups),
        )


class CandidateCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    support: tuple[SourceRef, ...]
    support_verdicts: tuple[SupportVerdict, ...]
    verdict: CandidateVerdict

    @property
    def status(self) -> FactStatus:
        domains = {ref.domain for ref in self.support}
        if len(domains) >= CONFIRMED_MIN_DOMAINS:
            return FactStatus.CONFIRMED
        return FactStatus.SINGLE


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
    candidate: ExtractedFact, snippets: Mapping[str, Snippet], limits: FactLimits
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
    if not support:
        outcome = CandidateVerdict.UNSUPPORTED
    elif not numbers_supported(candidate.text, (ref.quote for ref in support)):
        outcome = CandidateVerdict.NUMBER_MISMATCH
    else:
        outcome = CandidateVerdict.VERIFIED
    return CandidateCheck(
        text=candidate.text,
        support=tuple(support),
        support_verdicts=tuple(verdicts),
        verdict=outcome,
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
    if len(candidates) < DISPUTE_MIN_FACTS:
        return DisputeCheck()
    messages = render_dispute_check(
        [(candidate_id(index + 1), candidate.text) for index, candidate in enumerate(candidates)]
    )
    report = await client.complete_json(
        messages, ConflictReport, max_tokens=DISPUTE_CHECK_MAX_TOKENS
    )
    return collect_disputes(report, len(candidates))


def build_stats(
    *,
    snippets: int,
    snippets_truncated: int,
    candidates_over_limit: int,
    checks: Sequence[CandidateCheck],
    dispute_check: DisputeCheck,
    facts_disputed: int,
    facts_cut_by_limit: int,
    facts_kept: int,
) -> ExtractionStats:
    support = Counter(verdict for check in checks for verdict in check.support_verdicts)
    outcomes = Counter(check.verdict for check in checks)
    return ExtractionStats(
        snippets=snippets,
        snippets_truncated=snippets_truncated,
        candidates=len(checks),
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
        facts_cut_by_limit=facts_cut_by_limit,
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
    checks: list[CandidateCheck] = []
    over_limit = 0
    if shown:
        by_alias = {snippet_alias(index + 1): snippet for index, snippet in enumerate(shown)}
        reply = await client.complete_json(
            render_fact_extraction(topic, list(by_alias.items()), limits.min_quote_chars),
            ExtractedFacts,
            max_tokens=FACT_EXTRACTION_MAX_TOKENS,
        )
        over_limit = max(len(reply.facts) - FACT_CANDIDATES_MAX, 0)
        checks = [
            verify_candidate(candidate, by_alias, limits)
            for candidate in reply.facts[:FACT_CANDIDATES_MAX]
        ]
    verified = [check for check in checks if check.verdict is CandidateVerdict.VERIFIED]
    dispute_check = await find_disputes(client, verified)

    disputed = {index for dispute in dispute_check.disputes for index in dispute.members}
    assertable = sorted(
        (index for index in range(len(verified)) if index not in disputed),
        key=lambda index: STATUS_PRIORITY[verified[index].status],
    )
    kept = assertable[: limits.max_facts]
    ordered = kept + sorted(disputed)
    final_ids = {index: fact_id(position + 1) for position, index in enumerate(ordered)}
    facts = [
        Fact(
            id=final_ids[index],
            text=verified[index].text,
            support=list(verified[index].support),
            status=FactStatus.DISPUTED if index in disputed else verified[index].status,
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
        checks=checks,
        dispute_check=dispute_check,
        facts_disputed=len(disputed),
        facts_cut_by_limit=len(assertable) - len(kept),
        facts_kept=len(facts),
    )
    log_stats(stats)
    if len(kept) < limits.min_facts:
        return InsufficientFacts(
            fact_set=fact_set,
            assertable_count=len(kept),
            required=limits.min_facts,
            stats=stats,
        )
    return FactsExtracted(fact_set=fact_set, stats=stats)
