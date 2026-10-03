import re
from collections.abc import Iterable, Sequence
from functools import cache

from app.config import style
from app.config.constants import STYLE_EXCERPT_CONTEXT_WORDS
from app.domain.draft import Draft, LengthIssue, LengthViolation
from app.domain.style import StyleRule, Violation, ViolationSource
from app.prompts.style_critique import (
    BANNED_PHRASE_EXPLANATION,
    CLOSING_QUESTION_EXPLANATION,
    DASH_EXPLANATION,
    EMOJI_EXPLANATION,
    HASHTAG_EXPLANATION,
    INVENTED_EXPERIENCE_EXPLANATION,
    PART_TOO_LONG_EXPLANATION,
    TOO_FEW_FACTS_EXPLANATION,
    TOO_MANY_PARTS_EXPLANATION,
    TOO_SHORT_EXPLANATION,
    UNVERIFIED_NUMBER_EXPLANATION,
)
from app.services.short_post import split_sentences

PHRASE_TOKEN = re.compile(r"\w+")
YO = "ё"
YE = "е"
YE_CLASS = "[её]"
WORD_START = r"(?<!\w)"
WORD_END = r"(?!\w)"
WORD_TAIL = r"\w*"
OPTIONAL_SPACE = r"\s*"
LEFT_CONTEXT = re.compile(rf"(?:\S+\s+){{0,{STYLE_EXCERPT_CONTEXT_WORDS}}}\S*$")
RIGHT_CONTEXT = re.compile(rf"\S*(?:\s+\S+){{0,{STYLE_EXCERPT_CONTEXT_WORDS}}}")

type Span = tuple[int, int]


def fold_word(word: str) -> str:
    return word.lower().replace(YO, YE)


def literal(text: str) -> str:
    return "".join(YE_CLASS if char == YE else re.escape(char) for char in text)


def word_gap() -> str:
    return rf"[^\w{re.escape(style.SENTENCE_END_CHARACTERS)}]+"


def stem(word: str) -> str:
    for ending in sorted(style.PHRASE_STEM_ENDINGS, key=len, reverse=True):
        if word.endswith(ending) and len(word) - len(ending) >= style.PHRASE_MIN_STEM_CHARS:
            return word[: -len(ending)]
    return word


def word_pattern(token: str, exact_words: frozenset[str]) -> str:
    if token in style.PHRASE_PLACEHOLDERS:
        repeats = style.PHRASE_PLACEHOLDER_MAX_WORDS - 1
        return rf"\w+(?:{word_gap()}\w+){{0,{repeats}}}?"
    word = fold_word(token)
    if len(word) < style.PHRASE_MIN_STEM_CHARS or word in exact_words:
        return f"{literal(word)}{WORD_END}"
    return f"{literal(stem(word))}{WORD_TAIL}"


def separator_pattern(separator: str) -> str:
    marks = separator.strip()
    if not marks:
        return word_gap()
    return f"{OPTIONAL_SPACE}{re.escape(marks)}{OPTIONAL_SPACE}"


@cache
def phrase_pattern(phrase: str, exact_words: frozenset[str]) -> re.Pattern[str]:
    tokens = list(PHRASE_TOKEN.finditer(phrase))
    pieces: list[str] = [WORD_START]
    for index, token in enumerate(tokens):
        if index:
            pieces.append(separator_pattern(phrase[tokens[index - 1].end() : token.start()]))
        pieces.append(word_pattern(token.group(), exact_words))
    return re.compile("".join(pieces), re.IGNORECASE)


def overlaps(span: Span, taken: Iterable[Span]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)


def find_phrases(text: str, phrases: Sequence[str], exact_words: frozenset[str]) -> list[str]:
    matches = [
        match
        for phrase in phrases
        for match in phrase_pattern(phrase, exact_words).finditer(text)
        if match.group().strip()
    ]
    taken: list[Span] = []
    found: list[str] = []
    for match in sorted(matches, key=lambda item: (item.start(), -item.end())):
        if overlaps(match.span(), taken):
            continue
        taken.append(match.span())
        found.append(match.group())
    return found


def context(text: str, start: int, end: int) -> str:
    left = LEFT_CONTEXT.search(text[:start])
    right = RIGHT_CONTEXT.match(text, end)
    left_start = left.start() if left is not None else start
    right_end = right.end() if right is not None else end
    return text[left_start:right_end].strip()


def code_violation(
    rule: StyleRule, explanation: str, part: int | None, excerpt: str | None
) -> Violation:
    return Violation(
        rule=rule,
        source=ViolationSource.CODE,
        part=part,
        excerpt=excerpt,
        explanation=explanation,
    )


def check_dashes(texts: Sequence[str]) -> list[Violation]:
    violations: list[Violation] = []
    for part, text in enumerate(texts, start=1):
        for index, char in enumerate(text):
            if char in style.FORBIDDEN_DASHES:
                violations.append(
                    code_violation(
                        StyleRule.DASH,
                        DASH_EXPLANATION.format(dash=char, replacement=style.DASH_REPLACEMENT),
                        part,
                        context(text, index, index + 1),
                    )
                )
    return violations


def check_phrase_list(
    texts: Sequence[str],
    phrases: Sequence[str],
    rule: StyleRule,
    template: str,
) -> list[Violation]:
    return [
        code_violation(rule, template.format(phrase=found), part, found)
        for part, text in enumerate(texts, start=1)
        for found in find_phrases(text, phrases, style.BANNED_PHRASE_EXACT_WORDS)
    ]


def check_banned_phrases(texts: Sequence[str]) -> list[Violation]:
    return check_phrase_list(
        texts, style.BANNED_PHRASES, StyleRule.BANNED_PHRASE, BANNED_PHRASE_EXPLANATION
    )


def check_invented_experience(texts: Sequence[str]) -> list[Violation]:
    return check_phrase_list(
        texts,
        style.INVENTED_EXPERIENCE_PHRASES,
        StyleRule.INVENTED_EXPERIENCE,
        INVENTED_EXPERIENCE_EXPLANATION,
    )


def emoji_pattern() -> re.Pattern[str]:
    ranges = "".join(f"{re.escape(first)}-{re.escape(last)}" for first, last in style.EMOJI_RANGES)
    return re.compile(f"[{ranges}]+")


def hashtag_pattern() -> re.Pattern[str]:
    return re.compile(rf"(?<![\w&]){re.escape(style.HASHTAG_MARK)}\w+")


def check_emoji(texts: Sequence[str]) -> list[Violation]:
    pattern = emoji_pattern()
    return [
        code_violation(
            StyleRule.EMOJI, EMOJI_EXPLANATION, part, context(text, match.start(), match.end())
        )
        for part, text in enumerate(texts, start=1)
        for match in pattern.finditer(text)
    ]


def check_hashtags(texts: Sequence[str]) -> list[Violation]:
    pattern = hashtag_pattern()
    return [
        code_violation(StyleRule.HASHTAG, HASHTAG_EXPLANATION, part, match.group())
        for part, text in enumerate(texts, start=1)
        for match in pattern.finditer(text)
    ]


def check_closing_question(texts: Sequence[str], allowed: bool) -> list[Violation]:
    if allowed or not texts:
        return []
    last = texts[-1].rstrip()
    if not last.endswith(style.CLOSING_QUESTION_MARK):
        return []
    sentences = split_sentences(last)
    excerpt = sentences[-1] if sentences else last
    return [
        code_violation(
            StyleRule.CLOSING_QUESTION, CLOSING_QUESTION_EXPLANATION, len(texts), excerpt
        )
    ]


def length_explanation(violation: LengthViolation) -> str:
    match violation.issue:
        case LengthIssue.PART_TOO_LONG:
            return PART_TOO_LONG_EXPLANATION.format(
                part=violation.part, actual=violation.actual, limit=violation.limit
            )
        case LengthIssue.TOO_MANY_PARTS:
            return TOO_MANY_PARTS_EXPLANATION.format(actual=violation.actual, limit=violation.limit)
        case LengthIssue.TOO_SHORT:
            return TOO_SHORT_EXPLANATION.format(actual=violation.actual, limit=violation.limit)
        case LengthIssue.TOO_FEW_FACTS:
            return TOO_FEW_FACTS_EXPLANATION.format(actual=violation.actual, limit=violation.limit)


def check_length(draft: Draft) -> list[Violation]:
    return [
        code_violation(StyleRule.LENGTH, length_explanation(violation), violation.part, None)
        for violation in draft.length_violations
    ]


def check_numbers(draft: Draft) -> list[Violation]:
    return [
        code_violation(
            StyleRule.UNVERIFIED_NUMBER,
            UNVERIFIED_NUMBER_EXPLANATION.format(number=number),
            None,
            number,
        )
        for number in draft.unverified_numbers
    ]


def check_draft(draft: Draft, *, allow_closing_question: bool = False) -> list[Violation]:
    texts = draft.texts
    return [
        *check_dashes(texts),
        *check_banned_phrases(texts),
        *check_invented_experience(texts),
        *check_emoji(texts),
        *check_hashtags(texts),
        *check_closing_question(texts, allow_closing_question),
        *check_length(draft),
        *check_numbers(draft),
    ]
