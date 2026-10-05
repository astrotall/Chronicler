from collections.abc import Callable, Sequence
from itertools import pairwise
from typing import NamedTuple

from app.config.constants import STYLE_REMOVAL_MIN_COVERAGE
from app.config.style import DANGLING_OPENERS
from app.services.claim_survival import (
    WORD_PATTERN,
    contains_words,
    content_stems,
    normalized,
    stem_overlap,
)
from app.services.short_post import sentence_ends

SPACE_CHARACTERS = " \t"
NEWLINE = "\n"
SPACE = " "


class RemovalGuard(NamedTuple):
    min_parts: int
    min_chars: int
    keeps_enough: Callable[[int, int], bool]


class Removal(NamedTuple):
    texts: list[str]
    removed: list[str]
    blocked: int


class Span(NamedTuple):
    start: int
    end: int


def sentence_spans(text: str) -> list[Span]:
    bounds = [0, *sentence_ends(text), len(text)]
    spans: list[Span] = []
    for start, end in pairwise(bounds):
        piece = text[start:end]
        stripped = piece.strip()
        if stripped:
            offset = start + piece.index(stripped)
            spans.append(Span(offset, offset + len(stripped)))
    return spans


def cut_span(text: str, span: Span) -> str:
    left = text[: span.start].rstrip(SPACE_CHARACTERS)
    right = text[span.end :].lstrip(SPACE_CHARACTERS)
    if not left.strip():
        return right.lstrip()
    if not right.strip():
        return left.rstrip()
    if right.startswith(NEWLINE):
        return left.rstrip() + right
    if left.endswith(NEWLINE):
        return left + right
    return left + SPACE + right


def covers_sentence(excerpt: str, sentence: str) -> bool:
    sentence_text, excerpt_text = normalized(sentence), normalized(excerpt)
    if not (
        contains_words(sentence_text, excerpt_text) or contains_words(excerpt_text, sentence_text)
    ):
        return False
    return (
        stem_overlap(content_stems(sentence), content_stems(excerpt)) >= STYLE_REMOVAL_MIN_COVERAGE
    )


class FlaggedSentences(NamedTuple):
    part: int
    spans: list[Span]
    flagged: list[int]


def flagged_sentences(excerpt: str, texts: Sequence[str]) -> FlaggedSentences | None:
    for index, text in enumerate(texts):
        spans = sentence_spans(text)
        flagged = [
            position
            for position, span in enumerate(spans)
            if covers_sentence(excerpt, text[span.start : span.end])
        ]
        if flagged:
            return FlaggedSentences(index, spans, flagged)
    return None


def opens_dangling(sentence: str) -> bool:
    words = WORD_PATTERN.findall(normalized(sentence))
    return any(words[: len(opener.split())] == opener.split() for opener in DANGLING_OPENERS)


def following_sentence(texts: Sequence[str], found: FlaggedSentences, position: int) -> str | None:
    text = texts[found.part]
    after = position + 1
    while after in found.flagged:
        after += 1
    if after < len(found.spans):
        span = found.spans[after]
        return text[span.start : span.end]
    if found.part + 1 < len(texts):
        upcoming = sentence_spans(texts[found.part + 1])
        if upcoming:
            return texts[found.part + 1][upcoming[0].start : upcoming[0].end]
    return None


def leaves_dangling_reference(texts: Sequence[str], found: FlaggedSentences) -> bool:
    for position in found.flagged:
        following = following_sentence(texts, found, position)
        if following is not None and opens_dangling(following):
            return True
    return False


def apply_cut(text: str, spans: Sequence[Span]) -> str:
    for span in sorted(spans, reverse=True):
        text = cut_span(text, span)
    return text


def total_chars(texts: Sequence[str]) -> int:
    return sum(len(text) for text in texts)


def remove_flagged_sentences(
    texts: Sequence[str], excerpts: Sequence[str], guard: RemovalGuard
) -> Removal:
    current = list(texts)
    original_chars = total_chars(texts)
    removed: list[str] = []
    blocked = 0
    for excerpt in excerpts:
        found = flagged_sentences(excerpt, current)
        if found is None:
            continue
        if leaves_dangling_reference(current, found):
            blocked += 1
            continue
        index = found.part
        spans = [found.spans[position] for position in found.flagged]
        cut_text = apply_cut(current[index], spans)
        candidate = [
            *current[:index],
            *([cut_text] if cut_text.strip() else []),
            *current[index + 1 :],
        ]
        if len(candidate) < guard.min_parts:
            continue
        chars = total_chars(candidate)
        if chars < guard.min_chars or not guard.keeps_enough(original_chars, chars):
            continue
        removed.extend(current[index][span.start : span.end] for span in spans)
        current = candidate
    return Removal(current, removed, blocked)
