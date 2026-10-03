import pytest
from app.domain.draft import SentenceBudget
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.services.short_post import (
    drop_tail,
    mentions_disputed_numbers,
    select_short_facts,
    sentence_budget,
    sentences_to_cut,
    split_sentences,
)

TOPIC = "Куликовская битва"
EXPLANATION = "Источники расходятся."


def fact(fact_id: str, status: FactStatus = FactStatus.SINGLE, text: str = "Текст.") -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="s", url="https://example.org", domain="example.org", quote="q")
        ],
        status=status,
    )


def dispute(*fact_ids: str) -> Dispute:
    return Dispute(fact_ids=list(fact_ids), explanation=EXPLANATION)


def ids(fact_set: FactSet) -> list[str]:
    return [item.id for item in fact_set.facts]


def test_confirmed_facts_come_before_single_ones_in_fact_set_order() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1", FactStatus.SINGLE),
            fact("F2", FactStatus.CONFIRMED),
            fact("F3", FactStatus.SINGLE),
            fact("F4", FactStatus.CONFIRMED),
            fact("F5", FactStatus.SINGLE),
        ],
        disputes=[],
    )

    assert ids(select_short_facts(fact_set, 3)) == ["F1", "F2", "F4"]
    assert ids(select_short_facts(fact_set, 1)) == ["F2"]


def test_the_selection_keeps_the_topic_and_the_original_ids() -> None:
    fact_set = FactSet(topic=TOPIC, facts=[fact("F7"), fact("F9")], disputes=[])

    selected = select_short_facts(fact_set, 1)

    assert selected.topic == TOPIC
    assert ids(selected) == ["F7"]


def test_a_limit_above_the_fact_count_keeps_everything() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[fact("F1"), fact("F2", FactStatus.DISPUTED), fact("F3", FactStatus.DISPUTED)],
        disputes=[dispute("F2", "F3")],
    )

    assert select_short_facts(fact_set, 10) == fact_set


def test_a_dispute_is_left_out_when_the_assertable_facts_fill_the_limit() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1"),
            fact("F2"),
            fact("F3"),
            fact("F4", FactStatus.DISPUTED),
            fact("F5", FactStatus.DISPUTED),
        ],
        disputes=[dispute("F4", "F5")],
    )

    selected = select_short_facts(fact_set, 3)

    assert ids(selected) == ["F1", "F2", "F3"]
    assert selected.disputes == []


def test_a_dispute_enters_whole_when_its_pair_fits() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[fact("F1"), fact("F2", FactStatus.DISPUTED), fact("F3", FactStatus.DISPUTED)],
        disputes=[dispute("F2", "F3")],
    )

    selected = select_short_facts(fact_set, 3)

    assert ids(selected) == ["F1", "F2", "F3"]
    assert selected.disputes == fact_set.disputes


def test_a_dispute_is_never_split_when_only_one_slot_is_left() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1"),
            fact("F2"),
            fact("F3", FactStatus.DISPUTED),
            fact("F4", FactStatus.DISPUTED),
        ],
        disputes=[dispute("F3", "F4")],
    )

    assert ids(select_short_facts(fact_set, 3)) == ["F1", "F2"]


def test_overlapping_disputes_form_one_unit() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1"),
            fact("F2", FactStatus.DISPUTED),
            fact("F3", FactStatus.DISPUTED),
            fact("F4", FactStatus.DISPUTED),
        ],
        disputes=[dispute("F2", "F3"), dispute("F3", "F4")],
    )

    assert ids(select_short_facts(fact_set, 3)) == ["F1"]
    assert ids(select_short_facts(fact_set, 4)) == ["F1", "F2", "F3", "F4"]


def test_a_later_dispute_that_fits_is_taken_after_an_earlier_one_that_does_not() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1"),
            fact("F2", FactStatus.DISPUTED),
            fact("F3", FactStatus.DISPUTED),
            fact("F4", FactStatus.DISPUTED),
            fact("F5", FactStatus.DISPUTED),
            fact("F6", FactStatus.DISPUTED),
        ],
        disputes=[dispute("F2", "F3", "F4"), dispute("F5", "F6")],
    )

    assert ids(select_short_facts(fact_set, 3)) == ["F1", "F5", "F6"]


def test_disputed_facts_without_a_group_are_one_unit() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[fact("F1"), fact("F2", FactStatus.DISPUTED), fact("F3", FactStatus.DISPUTED)],
        disputes=[],
    )

    assert ids(select_short_facts(fact_set, 2)) == ["F1"]
    assert ids(select_short_facts(fact_set, 3)) == ["F1", "F2", "F3"]


def test_a_grouped_fact_counts_as_disputed_whatever_its_status() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[fact("F1", FactStatus.CONFIRMED), fact("F2", FactStatus.CONFIRMED), fact("F3")],
        disputes=[dispute("F1", "F2", "F9")],
    )

    assert ids(select_short_facts(fact_set, 2)) == ["F3"]


def test_only_disputed_facts_give_the_first_group_whole_even_over_the_limit() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            fact("F1", FactStatus.DISPUTED),
            fact("F2", FactStatus.DISPUTED),
            fact("F3", FactStatus.DISPUTED),
            fact("F4", FactStatus.DISPUTED),
        ],
        disputes=[dispute("F3", "F4"), dispute("F1", "F2")],
    )

    selected = select_short_facts(fact_set, 1)

    assert ids(selected) == ["F1", "F2"]
    assert selected.disputes == [dispute("F1", "F2")]


def test_an_empty_fact_set_selects_nothing() -> None:
    assert select_short_facts(FactSet(topic=TOPIC, facts=[], disputes=[]), 3).facts == []


@pytest.mark.parametrize(
    ("max_chars", "sentence_chars", "expected"),
    [
        (280, 80, SentenceBudget(max_sentences=3, sentence_chars=80)),
        (280, 140, SentenceBudget(max_sentences=2, sentence_chars=140)),
        (280, 279, SentenceBudget(max_sentences=1, sentence_chars=279)),
        (280, 400, SentenceBudget(max_sentences=1, sentence_chars=280)),
        (500, 80, SentenceBudget(max_sentences=6, sentence_chars=80)),
    ],
)
def test_the_sentence_budget_is_derived_from_the_limit(
    max_chars: int, sentence_chars: int, expected: SentenceBudget
) -> None:
    assert sentence_budget(max_chars, sentence_chars) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Первое. Второе! Третье? Четвёртое…", ["Первое.", "Второе!", "Третье?", "Четвёртое…"]),
        (
            "Князь Д. И. Донской победил. Мамай бежал.",
            ["Князь Д. И. Донской победил.", "Мамай бежал."],
        ),
        ("Битва была в 1380 г. Дмитрий победил.", ["Битва была в 1380 г. Дмитрий победил."]),
        ("Это было в XIV в. Потом всё изменилось.", ["Это было в XIV в. Потом всё изменилось."]),
        ("Улица им. Ленина. Дом снесли.", ["Улица им. Ленина.", "Дом снесли."]),
        ("Он сказал «Вперёд.» Войско пошло.", ["Он сказал «Вперёд.»", "Войско пошло."]),
        ("Год был 1380. 8 сентября пошли.", ["Год был 1380.", "8 сентября пошли."]),
        ("Итог был ясен... Мамай бежал.", ["Итог был ясен...", "Мамай бежал."]),
        ("Шли т.е. медленно. и тихо.", ["Шли т.е. медленно. и тихо."]),
        ("Первый абзац.\n\nВторой абзац.", ["Первый абзац.", "Второй абзац."]),
        ("Без точки в конце", ["Без точки в конце"]),
        ("", []),
    ],
    ids=[
        "punctuation",
        "initials",
        "year-abbreviation",
        "century-abbreviation",
        "listed-abbreviation",
        "closing-quote",
        "number-before-the-point",
        "ellipsis",
        "lowercase-after-point",
        "paragraphs",
        "no-final-point",
        "empty",
    ],
)
def test_sentences_are_split_without_cutting_one_in_two(text: str, expected: list[str]) -> None:
    assert split_sentences(text) == expected


def test_the_longest_sentence_is_named_when_it_covers_the_excess() -> None:
    text = "Короткое. Это самое длинное предложение поста. Среднее предложение."

    assert sentences_to_cut(text, 10) == ["Это самое длинное предложение поста."]


def test_several_sentences_are_named_in_text_order_when_one_is_not_enough() -> None:
    text = "Первое длинное предложение. Короткое. Второе длинное предложение тут."

    assert sentences_to_cut(text, 40) == [
        "Первое длинное предложение.",
        "Второе длинное предложение тут.",
    ]


def test_a_single_sentence_post_names_the_whole_sentence() -> None:
    assert sentences_to_cut("Одно предложение без конца", 5) == ["Одно предложение без конца"]


FIRST = "Первый абзац о битве."
SECOND = "Второй абзац. В нём два предложения."
THIRD = "Третий абзац."


def test_trailing_paragraphs_are_dropped_whole() -> None:
    text = f"{FIRST}\n\n{SECOND}\n\n{THIRD}"

    assert drop_tail(text, len(FIRST) + 10) == (FIRST, [SECOND, THIRD])
    assert drop_tail(text, len(f"{FIRST}\n\n{SECOND}")) == (f"{FIRST}\n\n{SECOND}", [THIRD])


def test_a_first_paragraph_over_the_limit_loses_its_trailing_sentences() -> None:
    first = "Начало. Середина длиннее. Конец абзаца."
    text = f"{first}\n\n{THIRD}"

    assert drop_tail(text, len("Начало. Середина длиннее.")) == (
        "Начало. Середина длиннее.",
        ["Конец абзаца.", THIRD],
    )
    assert drop_tail(text, 10) == ("Начало.", ["Середина длиннее. Конец абзаца.", THIRD])


def test_a_one_paragraph_post_is_trimmed_by_sentences() -> None:
    assert drop_tail("Первое. Второе. Третье.", 16) == ("Первое. Второе.", ["Третье."])


def test_nothing_is_dropped_when_the_first_sentence_is_over_the_limit() -> None:
    assert drop_tail("Очень длинное первое предложение. Второе.", 10) is None
    assert drop_tail("Одно длинное предложение без деления", 10) is None


def test_the_tail_is_never_cut_after_an_abbreviation() -> None:
    assert drop_tail("Это было в 1380 г. Дмитрий победил.", 20) is None


def test_a_cut_exactly_at_the_limit_is_kept() -> None:
    text = f"{FIRST}\n\n{THIRD}"

    assert drop_tail(text, len(FIRST)) == (FIRST, [THIRD])
    assert drop_tail(text, len(FIRST) - 1) is None


DISPUTED_SET = FactSet(
    topic=TOPIC,
    facts=[
        fact("F1", FactStatus.CONFIRMED, "Битва произошла 8 сентября 1380 года."),
        fact("F2", FactStatus.DISPUTED, "Войско оценивают в 60 000 человек."),
        fact("F3", FactStatus.DISPUTED, "Войско составляло 150 000 человек."),
    ],
    disputes=[dispute("F2", "F3")],
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Войско было в 60 000 человек.", True),
        ("Войско было в 60000 человек.", True),
        ("По другим данным 150 000.", True),
        ("Битва 8 сентября 1380 года.", False),
        ("Войско было шестьдесят тысяч человек.", False),
        ("Без чисел.", False),
    ],
    ids=["spaced", "unspaced", "other-version", "assertable-numbers", "in-words", "none"],
)
def test_numbers_of_disputed_facts_are_detected(text: str, expected: bool) -> None:
    assert mentions_disputed_numbers(text, DISPUTED_SET) is expected


def test_a_fact_flagged_disputed_without_a_group_counts_too() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[fact("F1", FactStatus.DISPUTED, "Топлива было на 25 секунд.")],
        disputes=[],
    )

    assert mentions_disputed_numbers("Оставалось 25 секунд.", fact_set)
