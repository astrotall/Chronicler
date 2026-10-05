import re
from collections.abc import Sequence
from enum import StrEnum
from typing import NamedTuple

from app.config.constants import (
    STYLE_FRAGMENT_MIN_STEMS,
    STYLE_FRAGMENT_MIN_WORD_CHARS,
    STYLE_FRAGMENT_STEM_CHARS,
)
from app.config.style import DELETABLE_STYLE_RULES
from app.domain.style import Violation
from app.services.quote_check import text_segments
from app.services.short_post import split_sentences

WORD_PATTERN = re.compile(r"\w+")
SEGMENT_JOINER = " "
WHOLE_WORDS_TEMPLATE = r"(?<!\w){needle}(?!\w)"


class Survival(StrEnum):
    EXACT = "exact"
    NEAR = "near"
    GONE = "gone"


class FragmentScore(NamedTuple):
    stems: int
    score: float
    verdict: Survival


class Survivor(NamedTuple):
    violation: Violation
    verdict: Survival


class ClosestSentence(NamedTuple):
    part: int
    sentence: str
    score: float


def is_deletable(violation: Violation) -> bool:
    return violation.rule.value in DELETABLE_STYLE_RULES and violation.excerpt is not None


def normalized(text: str) -> str:
    return SEGMENT_JOINER.join(text_segments(text))


def content_stems(text: str) -> frozenset[str]:
    stems: set[str] = set()
    for word in WORD_PATTERN.findall(normalized(text)):
        if any(character.isdigit() for character in word):
            stems.add(word)
        elif len(word) >= STYLE_FRAGMENT_MIN_WORD_CHARS:
            stems.add(word[:STYLE_FRAGMENT_STEM_CHARS])
    return frozenset(stems)


def contains_words(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    pattern = WHOLE_WORDS_TEMPLATE.format(needle=re.escape(needle))
    return re.search(pattern, haystack) is not None


def stem_overlap(fragment: frozenset[str], other: frozenset[str]) -> float:
    return len(fragment & other) / len(fragment) if fragment else 0.0


def exact_part(fragment: str, texts: Sequence[str]) -> int | None:
    needle = normalized(fragment)
    for part, text in enumerate(texts, start=1):
        if contains_words(normalized(text), needle):
            return part
    return None


def closest_sentence(fragment: str, texts: Sequence[str]) -> ClosestSentence | None:
    stems = content_stems(fragment)
    best: ClosestSentence | None = None
    for part, text in enumerate(texts, start=1):
        for sentence in split_sentences(text):
            score = stem_overlap(stems, content_stems(sentence))
            if best is None or score > best.score:
                best = ClosestSentence(part, sentence, score)
    return best


def score_fragment(fragment: str, texts: Sequence[str], threshold: float) -> FragmentScore:
    stems = content_stems(fragment)
    closest = closest_sentence(fragment, texts)
    score = 0.0 if closest is None else closest.score
    if exact_part(fragment, texts) is not None:
        verdict = Survival.EXACT
    elif len(stems) >= STYLE_FRAGMENT_MIN_STEMS and score >= threshold:
        verdict = Survival.NEAR
    else:
        verdict = Survival.GONE
    return FragmentScore(len(stems), score, verdict)


def find_survivors(
    flagged: Sequence[Violation], texts: Sequence[str], threshold: float
) -> list[Survivor]:
    survivors: list[Survivor] = []
    seen: set[tuple[int | None, str | None]] = set()
    for violation in flagged:
        if violation.excerpt is None:
            continue
        part = exact_part(violation.excerpt, texts)
        if part is not None:
            survivor = Survivor(violation.model_copy(update={"part": part}), Survival.EXACT)
        else:
            closest = closest_sentence(violation.excerpt, texts)
            if (
                closest is None
                or len(content_stems(violation.excerpt)) < STYLE_FRAGMENT_MIN_STEMS
                or closest.score < threshold
            ):
                continue
            survivor = Survivor(
                violation.model_copy(update={"part": closest.part, "excerpt": closest.sentence}),
                Survival.NEAR,
            )
        key = (survivor.violation.part, survivor.violation.excerpt)
        if key not in seen:
            seen.add(key)
            survivors.append(survivor)
    return survivors


def same_fragment(first: str, second: str, threshold: float) -> bool:
    first_text, second_text = normalized(first), normalized(second)
    if contains_words(first_text, second_text) or contains_words(second_text, first_text):
        return True
    first_stems, second_stems = content_stems(first), content_stems(second)
    smaller = min(len(first_stems), len(second_stems))
    if smaller < STYLE_FRAGMENT_MIN_STEMS:
        return False
    return len(first_stems & second_stems) / smaller >= threshold


def sentence_of(excerpt: str, text: str) -> str:
    needle = normalized(excerpt)
    for sentence in split_sentences(text):
        if contains_words(normalized(sentence), needle):
            return sentence
    return excerpt
