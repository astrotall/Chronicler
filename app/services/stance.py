import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from app.config import stance as stance_config
from app.domain.fact import ClaimStance, SourceRef
from app.services.quote_check import ELLIPSIS_PATTERN, QuoteCheck, check_quote
from app.services.short_post import split_sentences
from app.services.style_filter import phrase_pattern

SENTENCE_JOINER = " "
ANY_LENGTH = 1
CONTEXT_BREAK = re.compile(rf"{ELLIPSIS_PATTERN.pattern}|\n+")
LEADING_NOISE = " \t«\"„“'(—–-"


class StanceEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    marker: str
    context: str


class QuoteLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    own: str
    context: str


def parse_stance(value: str | None) -> ClaimStance | None:
    if value is None:
        return ClaimStance.ASSERTED
    try:
        return ClaimStance(value.strip().lower())
    except ValueError:
        return None


def stance_markers(stance: ClaimStance) -> tuple[str, ...]:
    match stance:
        case ClaimStance.ASSERTED:
            return ()
        case ClaimStance.CLAIMED:
            return stance_config.CLAIM_MARKERS
        case ClaimStance.REBUTTED:
            return stance_config.CLAIM_MARKERS + stance_config.REBUTTAL_MARKERS


def find_marker(text: str, markers: Sequence[str]) -> str | None:
    for marker in markers:
        if phrase_pattern(marker, stance_config.STANCE_MARKER_EXACT_WORDS).search(text):
            return marker
    return None


def opening_marker(text: str, markers: Sequence[str]) -> str | None:
    opening = text.lstrip(LEADING_NOISE)
    for marker in markers:
        if phrase_pattern(marker, stance_config.STANCE_MARKER_EXACT_WORDS).match(opening):
            return marker
    return None


def quote_span(quote: str, sentences: Sequence[str]) -> tuple[int, int] | None:
    width = max(len(split_sentences(quote)), 1)
    for size in (width, width + 1):
        for start in range(max(len(sentences) - size + 1, 0)):
            window = SENTENCE_JOINER.join(sentences[start : start + size])
            if check_quote(quote, window, ANY_LENGTH) is QuoteCheck.MATCH:
                return start, start + size - 1
    return None


def locate_quote(quote: str, snippet_text: str) -> QuoteLocation | None:
    reach = stance_config.STANCE_CONTEXT_SENTENCES
    for chunk in CONTEXT_BREAK.split(snippet_text):
        sentences = split_sentences(chunk)
        span = quote_span(quote, sentences)
        if span is not None:
            first, last = span
            return QuoteLocation(
                own=SENTENCE_JOINER.join(sentences[first : last + 1]),
                context=SENTENCE_JOINER.join(sentences[max(first - reach, 0) : last + reach + 1]),
            )
    return None


def quote_context(quote: str, snippet_text: str) -> str:
    location = locate_quote(quote, snippet_text)
    return quote if location is None else location.context


def opens_as_rebuttal(location: QuoteLocation) -> bool:
    return (
        opening_marker(location.own, stance_config.REBUTTAL_OPENERS) is not None
        and find_marker(location.own, stance_config.STRONG_CLAIM_MARKERS) is None
    )


def stance_evidence(stance: ClaimStance, quote: str, snippet_text: str) -> StanceEvidence | None:
    markers = stance_markers(stance)
    if not markers:
        return None
    context = quote_context(quote, snippet_text)
    marker = find_marker(context, markers)
    if marker is None:
        return None
    return StanceEvidence(marker=marker, context=context)


def strong_evidence(quote: str, snippet_text: str) -> StanceEvidence | None:
    location = locate_quote(quote, snippet_text)
    if location is not None and opens_as_rebuttal(location):
        return None
    context = quote if location is None else location.context
    marker = find_marker(context, stance_config.STRONG_CLAIM_MARKERS)
    if marker is None:
        return None
    return StanceEvidence(marker=marker, context=context)


def has_stance_evidence(
    stance: ClaimStance, support: Sequence[SourceRef], texts: Mapping[str, str]
) -> bool:
    return any(
        stance_evidence(stance, ref.quote, texts[ref.snippet_id]) is not None
        for ref in support
        if ref.snippet_id in texts
    )


def has_strong_evidence(support: Sequence[SourceRef], texts: Mapping[str, str]) -> bool:
    return any(
        strong_evidence(ref.quote, texts[ref.snippet_id]) is not None
        for ref in support
        if ref.snippet_id in texts
    )


def reads_as_rebuttal(support: Sequence[SourceRef], texts: Mapping[str, str]) -> bool:
    locations = [
        location
        for ref in support
        if ref.snippet_id in texts
        and (location := locate_quote(ref.quote, texts[ref.snippet_id])) is not None
    ]
    return bool(locations) and all(opens_as_rebuttal(location) for location in locations)
