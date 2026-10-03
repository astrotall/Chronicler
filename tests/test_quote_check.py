import pytest
from app.services.quote_check import (
    QuoteCheck,
    check_quote,
    extract_numbers,
    numbers_supported,
    text_segments,
)

SNIPPET = (
    "Кулико́вская битва (Мамаево побоище) — сражение 8 сентября 1380 года между\n"
    "войском Дмитрия Донского и войском беклярбека Мамая. В период «Великой замятни»\n"
    "Мамай управлял западной частью Золотой Орды."
)
TAVILY_SNIPPET = (
    "Мамай был темником Золотой Орды в 1360-1370-х годах. [...] "
    "Потерпел поражение на Куликовом поле в 1380 году."
)
MIN_CHARS = 20


def check(quote: str, snippet: str = SNIPPET, min_chars: int = MIN_CHARS) -> QuoteCheck:
    return check_quote(quote, snippet, min_chars)


def test_exact_quote_matches() -> None:
    assert check("сражение 8 сентября 1380 года между") is QuoteCheck.MATCH


@pytest.mark.parametrize(
    "quote",
    [
        "СРАЖЕНИЕ 8 сентября   1380 года между",
        "сражение 8 сентября 1380 года между войском",
        "1380 года между войском Дмитрия Донского",
        "года между\nвойском Дмитрия",
        "сражение 8 сентября 1380 года между",
    ],
)
def test_whitespace_and_case_do_not_matter(quote: str) -> None:
    assert check(quote) is QuoteCheck.MATCH


@pytest.mark.parametrize(
    "quote",
    [
        'В период "Великой замятни" Мамай',
        "В период “Великой замятни” Мамай",
        "В период „Великой замятни“ Мамай",
        "В период Великой замятни Мамай",
    ],
)
def test_quote_mark_variants_match(quote: str) -> None:
    assert check(quote) is QuoteCheck.MATCH


@pytest.mark.parametrize(
    "quote",
    [
        "Куликовская битва (Мамаево побоище) - сражение",
        "Куликовская битва (Мамаево побоище) – сражение",
        "Куликовская битва (Мамаево побоище)—сражение",
        "Куликовская битва (Мамаево побоище) − сражение",
    ],
)
def test_dash_variants_match(quote: str) -> None:
    assert check(quote) is QuoteCheck.MATCH


def test_stress_mark_in_snippet_is_ignored() -> None:
    assert check("Куликовская битва (Мамаево побоище)") is QuoteCheck.MATCH


def test_stress_mark_in_quote_is_ignored() -> None:
    plain = "Куликовская битва произошла на поле"

    assert check("Кулико́вская битва произошла на поле", plain) is QuoteCheck.MATCH


def test_yo_and_ye_are_equal() -> None:
    assert check("сражение 8 сентября 1380 года", SNIPPET.replace("е", "ё")) is QuoteCheck.MATCH
    assert check("Сражёние 8 сёнтября 1380 года") is QuoteCheck.MATCH


def test_trailing_punctuation_added_by_the_model_is_ignored() -> None:
    assert check("сражение 8 сентября 1380 года между.") is QuoteCheck.MATCH


@pytest.mark.parametrize(
    "quote",
    [
        "сражение 8 сентября 1381 года между",
        "сражение 7 сентября 1380 года между",
        "Мамай управлял всей Золотой Ордой",
        "Дмитрий Донской разбил войско Мамая",
        "сражение 8 сентября 1380 года при Непрядве",
    ],
)
def test_invented_or_almost_matching_quote_is_not_found(quote: str) -> None:
    assert check(quote) is QuoteCheck.NOT_FOUND


def test_quote_from_another_text_is_not_found() -> None:
    assert check("Потерпел поражение на Куликовом поле", SNIPPET) is QuoteCheck.NOT_FOUND


@pytest.mark.parametrize("quote", ["в 1380", "1380 года", "", "   ", "...", "«»", " — "])
def test_short_or_empty_quote_is_too_short(quote: str) -> None:
    assert check(quote) is QuoteCheck.TOO_SHORT


def test_quote_of_exactly_the_minimum_length_counts() -> None:
    quote = "сражение 8 сентября"

    assert check(quote, min_chars=len(quote)) is QuoteCheck.MATCH
    assert check(quote, min_chars=len(quote) + 1) is QuoteCheck.TOO_SHORT


def test_length_is_measured_after_normalisation() -> None:
    assert check("сражение   8   сентября", min_chars=20) is QuoteCheck.TOO_SHORT


def test_quote_inside_one_tavily_chunk_matches() -> None:
    assert check("Потерпел поражение на Куликовом поле", TAVILY_SNIPPET) is QuoteCheck.MATCH


def test_quote_across_the_tavily_joiner_is_not_found() -> None:
    quote = "в 1360-1370-х годах. Потерпел поражение"

    assert check(quote, TAVILY_SNIPPET) is QuoteCheck.NOT_FOUND


@pytest.mark.parametrize("ellipsis", ["...", "…", "[...]", " [...] "])
def test_quote_with_an_ellipsis_matches_parts_in_order(ellipsis: str) -> None:
    quote = f"Мамай был темником Золотой Орды{ellipsis}Потерпел поражение на Куликовом поле"

    assert check(quote, TAVILY_SNIPPET) is QuoteCheck.MATCH


def test_quote_parts_in_the_wrong_order_are_not_found() -> None:
    quote = "Потерпел поражение на Куликовом поле ... Мамай был темником Золотой Орды"

    assert check(quote, TAVILY_SNIPPET) is QuoteCheck.NOT_FOUND


def test_every_ellipsis_part_must_be_long_enough() -> None:
    quote = "Мамай был темником Золотой Орды ... в 1380 году"

    assert check(quote, TAVILY_SNIPPET) is QuoteCheck.TOO_SHORT


def test_leading_and_trailing_ellipsis_is_ignored() -> None:
    assert check("...Потерпел поражение на Куликовом поле…", TAVILY_SNIPPET) is QuoteCheck.MATCH


def test_segments_fold_typography() -> None:
    assert text_segments("Ёлка «Мамай» — 1320 – 1330 [...] далее…") == [
        "елка мамай-1320-1330",
        "далее",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("150 000", {"150000"}),
        ("150,000", {"150000"}),
        ("150.000", {"150000"}),
        ("150'000", {"150000"}),
        ("150’000", {"150000"}),
        ("150 000", {"150000"}),
        ("150 000", {"150000"}),
        ("150 000", {"150000"}),
        ("1,234,567", {"1234567"}),
        ("3,5", {"3.5"}),
        ("3.5", {"3.5"}),
        ("3,50", {"3.5"}),
        ("0,25", {"0.25"}),
        ("1.500", {"1500"}),
        ("1320-1330", {"1320", "1330"}),
        ("1320—1330", {"1320", "1330"}),
        ("1380 300", {"1380", "300"}),
        ("1380, 1381", {"1380", "1381"}),
        ("8 сентября 1380 года.", {"8", "1380"}),
        ("08", {"8"}),
        ("в 1360-1370-х годах", {"1360", "1370"}),
        ("без чисел", set()),
        ("двенадцать", set()),
    ],
)
def test_extract_numbers(text: str, expected: set[str]) -> None:
    assert extract_numbers(text) == expected


@pytest.mark.parametrize(
    ("fact_text", "quotes"),
    [
        ("Русское войско насчитывало 150 000 человек", ["the army numbered 150,000 men"]),
        ("Русское войско насчитывало 150000 человек", ["войско насчитывало 150 000 человек"]),
        ("Войско насчитывало 150,000 человек", ["войско насчитывало 150.000 человек"]),
        ("Потери составили 3,5 тысячи", ["losses were 3.5 thousand"]),
        ("Мамай правил в 1361—1380 годах", ["from 1361 to 1380"]),
        ("Мамай правил в 1361-1380 годах", ["С 1361 по 1380 год"]),
        ("Битва была 8 сентября 1380 года", ["8 сентября", "в 1380 году"]),
        ("Мамай был беклярбеком", ["Мамай был беклярбеком Золотой Орды"]),
        ("Войско насчитывало 1500 человек", ["войско насчитывало 1.500 человек"]),
    ],
)
def test_numbers_supported(fact_text: str, quotes: list[str]) -> None:
    assert numbers_supported(fact_text, quotes)


@pytest.mark.parametrize(
    ("fact_text", "quotes"),
    [
        ("Русское войско насчитывало 150 000 человек", ["войско насчитывало 15 000 человек"]),
        ("Русское войско насчитывало 150 000 человек", ["the army numbered 150 men"]),
        ("Потери составили 3,5 тысячи", ["losses were 35 thousand"]),
        ("Мамай правил в 1361—1381 годах", ["from 1361 to 1380"]),
        ("Битва была в 1380 году", ["в тысяча триста восьмидесятом году"]),
        ("Битва была 8 сентября 1380 года", ["в 1380 году"]),
        ("Войско насчитывало 1,5 тысячи", ["войско насчитывало 1.500 человек"]),
        ("Битва была в 1380 году", []),
    ],
)
def test_numbers_not_supported(fact_text: str, quotes: list[str]) -> None:
    assert not numbers_supported(fact_text, quotes)
