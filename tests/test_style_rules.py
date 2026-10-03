import re

import pytest
from app.config import style
from app.prompts import style_rules
from app.prompts.style_rules import render_style_rules

RULE_NUMBER = re.compile(r"^(\d+)\. ", re.MULTILINE)
RULE_COUNT = 16

EXAMPLES = (
    style.DASH_EXAMPLE,
    style.INVENTED_MEANING_EXAMPLE,
    style.NUMBER_DIGITS_EXAMPLE,
)


def rule(number: int) -> str:
    [text] = [line for line in render_style_rules().splitlines() if line.startswith(f"{number}. ")]
    return text


def test_rules_are_numbered_without_gaps() -> None:
    numbers = [int(number) for number in RULE_NUMBER.findall(render_style_rules())]

    assert numbers == list(range(1, RULE_COUNT + 1))


def test_every_placeholder_is_filled() -> None:
    rendered = render_style_rules()

    assert "{" not in rendered
    assert "}" not in rendered


def test_first_person_is_not_a_requirement() -> None:
    assert "пост рассказывает о фактах, а не об авторе" in rule(1)


def test_opinion_is_rare_and_capped_from_config() -> None:
    text = rule(2)

    assert "допустимо, но редко" in text
    assert f"целиком: {style.OPINION_MAX_PER_POST}," in text
    assert "может обойтись без мнения" in text
    assert "Никогда не заканчивай мнением часть поста" in text
    assert f"Плохо: «{style.OPINION_CLOSER_EXAMPLE}»" in text


def has_forbidden_dash(text: str) -> bool:
    return any(dash in text for dash in style.FORBIDDEN_DASHES)


def test_dash_rule_offers_the_spaced_hyphen() -> None:
    text = rule(4)

    assert "Длинное и среднее тире (—, –) не используй" in text
    assert f"Где нужно тире, ставь дефис с пробелами ({style.DASH_REPLACEMENT})" in text
    assert f"Плохо: «{style.DASH_EXAMPLE.bad}»" in text
    assert f"Хорошо: «{style.DASH_EXAMPLE.good}»" in text


def test_dash_rule_keeps_the_hyphen_for_dashes_only() -> None:
    text = rule(4)

    assert "заменяет только тире" in text
    assert "не делай знаком препинания по умолчанию" in text
    assert "точка, запятая или двоеточие" not in text
    assert "ставь запятую на место тире" not in text


def test_spaced_hyphen_is_not_a_forbidden_dash() -> None:
    assert style.DASH_REPLACEMENT.strip() == "-"
    assert not has_forbidden_dash(style.DASH_REPLACEMENT)


@pytest.mark.parametrize(
    "text",
    [
        "Победа - это начало.",
        "Кто-то пришёл по-петровски.",
        "Храм строили в 1320-1330 годах.",
        "",
    ],
    ids=["spaced", "inside-word", "range", "empty"],
)
def test_plain_hyphen_passes(text: str) -> None:
    assert not has_forbidden_dash(text)


@pytest.mark.parametrize(
    "text",
    [
        "Победа — это начало.",
        "Победа – это начало.",
        "Победа—это начало.",
        "Храм строили в 1320–1330 годах.",
    ],
    ids=["em-spaced", "en-spaced", "em-tight", "en-range"],
)
def test_long_and_medium_dashes_are_caught(text: str) -> None:
    assert has_forbidden_dash(text)


def test_dash_example_shows_the_forbidden_dash_and_its_replacement() -> None:
    assert has_forbidden_dash(style.DASH_EXAMPLE.bad)
    assert style.DASH_REPLACEMENT in style.DASH_EXAMPLE.good


def test_filler_closers_are_listed_from_config() -> None:
    text = rule(13)

    assert "пересказать, прокомментировать или оценить предыдущее" in text
    for example in style.FILLER_CLOSER_EXAMPLES:
        assert f"«{example}»" in text


def test_no_meaning_beyond_the_facts() -> None:
    text = rule(14)

    assert "выводов, причин, следствий и оценок значимости, которых нет в фактах" in text
    assert f"Плохо: «{style.INVENTED_MEANING_EXAMPLE.bad}»" in text
    assert f"Хорошо: «{style.INVENTED_MEANING_EXAMPLE.good}»" in text


def test_numbers_in_digits_with_small_counts_allowed_in_words() -> None:
    text = rule(15)

    assert "Даты, годы, сроки, суммы, размеры, возрасты и проценты пиши цифрами" in text
    assert f"«{style.SMALL_COUNT_IN_WORDS_EXAMPLE}»" in text
    assert "Не высчитывай промежутки, сроки и количества, которых нет в фактах" in text
    assert f"Плохо: «{style.NUMBER_DIGITS_EXAMPLE.bad}»" in text
    assert f"Хорошо: «{style.NUMBER_DIGITS_EXAMPLE.good}»" in text


def test_thread_structure_rule_keeps_tweets_whole() -> None:
    text = rule(16)

    assert "«один факт на твит и завершающий оборот у каждого»" in text
    assert "одним простым предложением" in text
    assert "твит не обрывок" in text


def test_rules_follow_the_config_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(style_rules, "OPINION_MAX_PER_POST", 2)
    monkeypatch.setattr(
        style_rules, "FILLER_CLOSER_EXAMPLES", ("Новая заглушка.", "Ещё одна заглушка.")
    )

    assert "целиком: 2," in rule(2)
    assert "«Новая заглушка.», «Ещё одна заглушка.»" in rule(13)
    assert style.FILLER_CLOSER_EXAMPLES[0] not in render_style_rules()


def test_rule_data_is_not_empty() -> None:
    assert style.OPINION_MAX_PER_POST >= 1
    assert style.FILLER_CLOSER_EXAMPLES
    assert all(text.strip() for text in style.FILLER_CLOSER_EXAMPLES)
    assert style.OPINION_CLOSER_EXAMPLE.strip()
    assert style.SMALL_COUNT_IN_WORDS_EXAMPLE.strip()
    for example in EXAMPLES:
        assert example.bad.strip()
        assert example.good.strip()
        assert example.bad != example.good


@pytest.mark.parametrize("example", EXAMPLES, ids=["dash", "meaning", "digits"])
def test_good_examples_obey_the_deterministic_rules(example: style.RuleExample) -> None:
    good = example.good.casefold()

    assert not has_forbidden_dash(good)
    assert not any(phrase.casefold() in good for phrase in style.BANNED_PHRASES)
    assert not any(phrase in good for phrase in style.INVENTED_EXPERIENCE_PHRASES)
