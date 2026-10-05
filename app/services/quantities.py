import re
import unicodedata
from collections.abc import Iterable, Sequence
from fractions import Fraction
from functools import cache
from itertools import pairwise
from typing import NamedTuple

from app.config import quantities as data
from app.config.quantities import QuantityKind, QuantityPhrase, WordForms
from app.config.style import SENTENCE_END_CHARACTERS
from app.services.quote_check import (
    COMPATIBILITY_FORM,
    DECOMPOSED_FORM,
    INVISIBLE_CHARACTERS,
    NUMBER_TOKEN,
    SINGLE_SPACE,
    STRESS_MARKS,
    WHITESPACE_PATTERN,
    extract_numbers,
    split_number_token,
)
from app.services.short_post import PARAGRAPH_BREAK, sentence_ends
from app.services.style_filter import WORD_END, WORD_START, fold_word, literal

CLEANED_CHARACTERS = str.maketrans("", "", STRESS_MARKS + INVISIBLE_CHARACTERS)
WORD_SEPARATOR = r"(?:\s*[-‐‑]\s*|\s+)"
CONTEXT_GAP = rf"[^\w{re.escape(SENTENCE_END_CHARACTERS)}]*"
PREVIOUS_WORD = re.compile(rf"(\w+){CONTEXT_GAP}$")
NEXT_WORD = re.compile(rf"{CONTEXT_GAP}(\w+)")

type Span = tuple[int, int]


class Amount(NamedTuple):
    kind: QuantityKind
    value: Fraction


class Quantity(NamedTuple):
    kind: QuantityKind
    value: Fraction
    form: str
    start: int
    end: int

    @property
    def amount(self) -> Amount:
        return Amount(self.kind, self.value)


class QuantityCheck(NamedTuple):
    unsupported: list[str]
    share_sets: list[list[str]]


def clean_text(text: str) -> str:
    decomposed = unicodedata.normalize(DECOMPOSED_FORM, text).translate(CLEANED_CHARACTERS)
    return unicodedata.normalize(COMPATIBILITY_FORM, decomposed)


def exact_tolerance(tolerance: float) -> Fraction:
    return Fraction(str(tolerance))


def same_value(first: Fraction, second: Fraction, tolerance: Fraction) -> bool:
    return abs(first - second) <= tolerance * max(abs(first), abs(second))


def supported(amount: Amount, sources: Iterable[Amount], tolerance: Fraction) -> bool:
    return any(
        source.kind is amount.kind and same_value(source.value, amount.value, tolerance)
        for source in sources
    )


def word_regex(word: WordForms) -> str:
    endings = sorted(word.endings, key=len, reverse=True)
    return f"{literal(word.stem)}(?:{'|'.join(literal(ending) for ending in endings)})"


def words_regex(words: Iterable[WordForms]) -> str:
    return "|".join(word_regex(word) for word in words)


@cache
def phrase_pattern(phrase: QuantityPhrase) -> re.Pattern[str]:
    body = WORD_SEPARATOR.join(word_regex(word) for word in phrase.words)
    return re.compile(f"{WORD_START}{body}{WORD_END}", re.IGNORECASE)


@cache
def percent_pattern() -> re.Pattern[str]:
    signs = "|".join(re.escape(sign) for sign in data.PERCENT_SIGNS)
    words = words_regex(data.PERCENT_WORDS)
    return re.compile(
        rf"({NUMBER_TOKEN.pattern})\s*(?:{signs}|(?:{words}){WORD_END})", re.IGNORECASE
    )


@cache
def times_pattern() -> re.Pattern[str]:
    words = words_regex(data.TIMES_WORDS)
    return re.compile(rf"({NUMBER_TOKEN.pattern})\s+(?:{words}){WORD_END}", re.IGNORECASE)


@cache
def slash_pattern() -> re.Pattern[str]:
    slashes = "".join(re.escape(slash) for slash in data.FRACTION_SLASHES)
    return re.compile(rf"(?<![\d.,{slashes}])(\d+)\s*[{slashes}]\s*(\d+)(?![\d{slashes}])")


def forms_of(words: Iterable[WordForms]) -> frozenset[str]:
    return frozenset(fold_word(f"{word.stem}{ending}") for word in words for ending in word.endings)


@cache
def period_forms() -> frozenset[str]:
    return forms_of(data.PERIOD_ORDINALS)


@cache
def unit_forms() -> frozenset[str]:
    return forms_of((*data.TIME_UNITS, *data.CLOCK_HOURS))


def previous_word(text: str, start: int) -> str | None:
    match = PREVIOUS_WORD.search(text, 0, start)
    return fold_word(match.group(1)) if match is not None else None


def next_content_word(text: str, end: int) -> str | None:
    position = end
    while (match := NEXT_WORD.match(text, position)) is not None:
        word = fold_word(match.group(1))
        if word not in data.SKIPPED_BEFORE_UNIT:
            return word
        position = match.end()
    return None


def excluded_share(text: str, match: re.Match[str]) -> bool:
    before = previous_word(text, match.start())
    if before in period_forms() or next_content_word(text, match.end()) in unit_forms():
        return True
    return (
        fold_word(match.group()) in data.QUALIFIER_REQUIRED_FORMS
        and before not in data.SHARE_QUALIFIERS
    )


def display_form(text: str) -> str:
    return WHITESPACE_PATTERN.sub(SINGLE_SPACE, text).strip()


def overlaps(span: Span, taken: Iterable[Span]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in taken)


def without_overlaps(found: Iterable[Quantity]) -> list[Quantity]:
    kept: list[Quantity] = []
    for quantity in sorted(found, key=lambda item: (item.start, -item.end)):
        if not overlaps((quantity.start, quantity.end), ((q.start, q.end) for q in kept)):
            kept.append(quantity)
    return kept


def word_quantities(text: str, phrases: Sequence[QuantityPhrase]) -> list[Quantity]:
    found: list[Quantity] = []
    for phrase in phrases:
        for match in phrase_pattern(phrase).finditer(text):
            if phrase.kind is QuantityKind.SHARE and excluded_share(text, match):
                continue
            found.append(
                Quantity(
                    phrase.kind,
                    phrase.value,
                    display_form(match.group()),
                    match.start(),
                    match.end(),
                )
            )
    return without_overlaps(found)


def token_value(token: str) -> Fraction:
    return Fraction(split_number_token(token)[-1])


def percent_quantities(text: str) -> list[Quantity]:
    return [
        Quantity(
            QuantityKind.SHARE,
            token_value(match.group(1)) / data.PERCENT_BASE,
            display_form(match.group()),
            match.start(),
            match.end(),
        )
        for match in percent_pattern().finditer(text)
    ]


def slash_quantities(text: str) -> list[Quantity]:
    found: list[Quantity] = []
    for match in slash_pattern().finditer(text):
        numerator, denominator = int(match.group(1)), int(match.group(2))
        if 0 < numerator < denominator <= data.FRACTION_MAX_DENOMINATOR:
            found.append(
                Quantity(
                    QuantityKind.SHARE,
                    Fraction(numerator, denominator),
                    display_form(match.group()),
                    match.start(),
                    match.end(),
                )
            )
    return found


def times_quantities(text: str) -> list[Quantity]:
    return [
        Quantity(
            QuantityKind.MULTIPLE,
            token_value(match.group(1)),
            display_form(match.group()),
            match.start(),
            match.end(),
        )
        for match in times_pattern().finditer(text)
    ]


def digit_shares(text: str) -> list[Quantity]:
    return [*percent_quantities(text), *slash_quantities(text)]


def checked_quantities(text: str) -> list[Quantity]:
    return word_quantities(clean_text(text), data.RUSSIAN_PHRASES)


def amounts_of(text: str, *, english: bool) -> set[Amount]:
    cleaned = clean_text(text)
    phrases = (*data.RUSSIAN_PHRASES, *data.ENGLISH_PHRASES) if english else data.RUSSIAN_PHRASES
    found = [*word_quantities(cleaned, phrases), *digit_shares(cleaned), *times_quantities(cleaned)]
    amounts = {quantity.amount for quantity in found}
    amounts |= {
        Amount(QuantityKind.NUMBER, Fraction(number)) for number in extract_numbers(cleaned)
    }
    return amounts


def all_amounts(texts: Iterable[str], *, english: bool) -> set[Amount]:
    amounts: set[Amount] = set()
    for text in texts:
        amounts |= amounts_of(text, english=english)
    return amounts


def quantities_supported(fact_text: str, quotes: Iterable[str], tolerance: float) -> bool:
    exact = exact_tolerance(tolerance)
    sources = all_amounts(quotes, english=True)
    return all(
        supported(quantity.amount, sources, exact) for quantity in checked_quantities(fact_text)
    )


def segment_spans(text: str) -> list[Span]:
    breaks = list(PARAGRAPH_BREAK.finditer(text))
    starts = [0, *(match.end() for match in breaks)]
    ends = [*(match.start() for match in breaks), len(text)]
    spans: list[Span] = []
    for start, end in zip(starts, ends, strict=True):
        spans.append((start, end))
        cuts = [start, *(start + cut for cut in sentence_ends(text[start:end])), end]
        spans.extend(pairwise(cuts))
    return spans


def whole_share_groups(
    text: str, shares: Sequence[Quantity], tolerance: Fraction
) -> list[list[Quantity]]:
    groups: list[list[Quantity]] = []
    seen: set[tuple[int, ...]] = set()
    for start, end in segment_spans(text):
        members = [share for share in shares if start <= share.start and share.end <= end]
        key = tuple(member.start for member in members)
        if len(members) < data.SHARE_SUM_MIN_MEMBERS or key in seen:
            continue
        seen.add(key)
        total = sum((member.value for member in members), Fraction(0))
        if same_value(total, data.SHARE_WHOLE, tolerance):
            groups.append(members)
    return groups


def add_form(forms: list[str], form: str) -> None:
    if fold_word(form) not in {fold_word(known) for known in forms}:
        forms.append(form)


def check_post_quantities(
    texts: Sequence[str], fact_texts: Sequence[str], tolerance: float
) -> QuantityCheck:
    exact = exact_tolerance(tolerance)
    sources = all_amounts(fact_texts, english=False)
    unsupported: list[str] = []
    share_sets: list[list[str]] = []
    for text in texts:
        cleaned = clean_text(text)
        words = word_quantities(cleaned, data.RUSSIAN_PHRASES)
        flagged = [word for word in words if not supported(word.amount, sources, exact)]
        shares = sorted(
            [*(word for word in words if word.kind is QuantityKind.SHARE), *digit_shares(cleaned)],
            key=lambda quantity: quantity.start,
        )
        for group in whole_share_groups(cleaned, shares, exact):
            if all(supported(member.amount, sources, exact) for member in group):
                continue
            forms = [member.form for member in group]
            if forms not in share_sets:
                share_sets.append(forms)
            flagged.extend(group)
        for quantity in sorted(flagged, key=lambda item: item.start):
            add_form(unsupported, quantity.form)
    return QuantityCheck(unsupported, share_sets)
