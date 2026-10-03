import re
import unicodedata
from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Final, Literal

DECOMPOSED_FORM: Final[Literal["NFD"]] = "NFD"
COMPATIBILITY_FORM: Final[Literal["NFKC"]] = "NFKC"
STRESS_MARKS = "́̀"
INVISIBLE_CHARACTERS = "­​‌‍⁠﻿"
QUOTE_CHARACTERS = "\"'`«»„“”‟‚‘’‛‹›"
REMOVED_CHARACTERS = str.maketrans("", "", STRESS_MARKS + INVISIBLE_CHARACTERS + QUOTE_CHARACTERS)
YO = "ё"
YE = "е"
DASH_PATTERN = re.compile(r"\s*[-‐‑‒–—―−]\s*")
CANONICAL_DASH = "-"
ELLIPSIS_PATTERN = re.compile(r"\[\s*\.{3,}\s*\]|\.{3,}")
WHITESPACE_PATTERN = re.compile(r"\s+")
SINGLE_SPACE = " "
EDGE_PUNCTUATION = " .,;:!?"

NUMBER_TOKEN = re.compile(r"[0-9]+(?:[    ,.'’][0-9]+)*")
NUMBER_PART = re.compile(r"[0-9]+|[^0-9]")
THOUSANDS_GROUP_DIGITS = 3
DECIMAL_SEPARATORS = frozenset({",", "."})
CANONICAL_DECIMAL_SEPARATOR = "."
ZERO_DIGIT = "0"


class QuoteCheck(StrEnum):
    MATCH = "match"
    TOO_SHORT = "too_short"
    NOT_FOUND = "not_found"


def fold_text(text: str) -> str:
    decomposed = unicodedata.normalize(DECOMPOSED_FORM, text).translate(REMOVED_CHARACTERS)
    return unicodedata.normalize(COMPATIBILITY_FORM, decomposed).casefold().replace(YO, YE)


def normalize_segment(segment: str) -> str:
    dashed = DASH_PATTERN.sub(CANONICAL_DASH, segment)
    return WHITESPACE_PATTERN.sub(SINGLE_SPACE, dashed).strip(EDGE_PUNCTUATION)


def text_segments(text: str) -> list[str]:
    segments = (normalize_segment(part) for part in ELLIPSIS_PATTERN.split(fold_text(text)))
    return [segment for segment in segments if segment]


def contains_in_order(parts: Sequence[str], segments: Sequence[str]) -> bool:
    segment_index = 0
    position = 0
    for part in parts:
        while segment_index < len(segments):
            found = segments[segment_index].find(part, position)
            if found >= 0:
                position = found + len(part)
                break
            segment_index += 1
            position = 0
        else:
            return False
    return True


def check_quote(quote: str, snippet_text: str, min_chars: int) -> QuoteCheck:
    parts = text_segments(quote)
    if not parts or any(len(part) < min_chars for part in parts):
        return QuoteCheck.TOO_SHORT
    if contains_in_order(parts, text_segments(snippet_text)):
        return QuoteCheck.MATCH
    return QuoteCheck.NOT_FOUND


def canonical_number(integer: str, fraction: str) -> str:
    whole = integer.lstrip(ZERO_DIGIT) or ZERO_DIGIT
    decimals = fraction.rstrip(ZERO_DIGIT)
    return f"{whole}{CANONICAL_DECIMAL_SEPARATOR}{decimals}" if decimals else whole


def split_number_token(token: str) -> list[str]:
    parts = NUMBER_PART.findall(token)
    groups = parts[0::2]
    separators = parts[1::2]
    numbers: list[str] = []
    integer = groups[0]
    fraction: str | None = None
    grouped = len(integer) <= THOUSANDS_GROUP_DIGITS
    for separator, group in zip(separators, groups[1:], strict=True):
        if fraction is None and grouped and len(group) == THOUSANDS_GROUP_DIGITS:
            integer += group
        elif (
            fraction is None
            and separator in DECIMAL_SEPARATORS
            and len(group) != THOUSANDS_GROUP_DIGITS
        ):
            fraction = group
        else:
            numbers.append(canonical_number(integer, fraction or ""))
            integer = group
            fraction = None
            grouped = len(group) <= THOUSANDS_GROUP_DIGITS
    numbers.append(canonical_number(integer, fraction or ""))
    return numbers


def extract_numbers(text: str) -> set[str]:
    normalized = unicodedata.normalize(COMPATIBILITY_FORM, text)
    return {
        number for token in NUMBER_TOKEN.findall(normalized) for number in split_number_token(token)
    }


def numbers_supported(fact_text: str, quotes: Iterable[str]) -> bool:
    available: set[str] = set()
    for quote in quotes:
        available |= extract_numbers(quote)
    return extract_numbers(fact_text) <= available
