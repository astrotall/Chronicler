from collections.abc import Sequence

import pytest
from app.config.constants import (
    RELEVANCE_CHECK_MAX_TOKENS,
    RELEVANCE_MAX_ASPECTS,
    RELEVANCE_OTHER_ASPECT,
)
from app.domain.fact import ClaimStance, FactExtraction, FactsExtracted, FactStatus
from app.domain.llm import Role
from app.domain.snippet import Snippet
from app.llm.errors import LLMUnavailableError
from app.prompts.fact_relevance import render_relevance_check
from app.services.fact_relevance import (
    FactRelevance,
    Period,
    RelevanceCheck,
    RelevanceEntry,
    RelevanceReport,
    cap_aspects,
    check_period,
    dated_outside,
    normalize_aspect,
    topic_period,
)
from app.services.facts import FactLimits, collect_relevance, extract_facts
from app.services.short_post import select_short_facts

from fact_helpers import NO_CONFLICTS, conflict, conflicts, extraction, fact, make_limits, support
from llm_helpers import ScriptedLLMClient, as_client
from research_helpers import make_snippet

TOPIC = "Как жили горожане в 1930-е годы"
EXHIBITION_QUOTE = "Экспозиция выставки показывает плакаты той эпохи"
FRIDGE_QUOTE = "В 1976 году холодильник был у двух третей семей"
COMMUNAL_QUOTE = "В 1930-е годы горожане жили в коммунальных квартирах"
ROOM_QUOTE = "На семью выделяли одну комнату с общей кухней"
CARDS_QUOTE = "Хлеб выдавали по карточкам до 1935 года"
WEEK_QUOTE = "Рабочая неделя длилась шесть дней"
EMPTY_QUOTE = "Эта версия позднее была опровергнута"
MYTH_QUOTE = "в календаре 1930 года было 30 февраля"
REBUTTAL_QUOTE = "сохранившиеся табели 1930 года показывают обычный февраль из 28 дней"

LIFE_SITE = make_snippet(
    "https://life.example.org/1930s",
    f"{EXHIBITION_QUOTE}. {FRIDGE_QUOTE}. {COMMUNAL_QUOTE}. {ROOM_QUOTE}. {CARDS_QUOTE}. "
    f"{WEEK_QUOTE}. {EMPTY_QUOTE}.",
)
MYTH_SITE = make_snippet(
    "https://calendar.example.net/reform",
    f"Многие источники пишут, что {MYTH_QUOTE}. Однако {REBUTTAL_QUOTE}.",
)
SNIPPETS: list[Snippet] = [LIFE_SITE, MYTH_SITE]

EXHIBITION = "Экспозиция выставки показывает плакаты той эпохи."
FRIDGE = "В 1976 году холодильник был у двух третей семей."
COMMUNAL = "В 1930-е годы горожане жили в коммунальных квартирах."
ROOM = "На семью выделяли одну комнату с общей кухней."
CARDS = "Хлеб выдавали по карточкам до 1935 года."
WEEK = "Рабочая неделя длилась шесть дней."
EMPTY = "Опровергнуто"
MYTH = "По распространённому утверждению, в календаре 1930 года было 30 февраля."
REBUTTAL = "Сохранившиеся табели 1930 года показывают обычный февраль из 28 дней."

LIFE_REPLY = extraction(
    fact(EXHIBITION, support("S1", EXHIBITION_QUOTE)),
    fact(FRIDGE, support("S1", FRIDGE_QUOTE)),
    fact(COMMUNAL, support("S1", COMMUNAL_QUOTE)),
    fact(ROOM, support("S1", ROOM_QUOTE)),
    fact(CARDS, support("S1", CARDS_QUOTE)),
    fact(WEEK, support("S1", WEEK_QUOTE)),
)
MODEL_ORDER = [EXHIBITION, FRIDGE, COMMUNAL, ROOM, CARDS, WEEK]
THREE_FACTS = [(COMMUNAL, COMMUNAL_QUOTE), (CARDS, CARDS_QUOTE), (WEEK, WEEK_QUOTE)]


def entry(
    fact_id: str,
    relevance: int,
    *,
    aspect: str = "",
    about_source: bool = False,
    outside_period: bool = False,
) -> dict[str, object]:
    return {
        "id": fact_id,
        "aspect": aspect,
        "about_source": about_source,
        "outside_period": outside_period,
        "relevance": relevance,
    }


def ranking(*entries: dict[str, object]) -> dict[str, object]:
    return {"facts": list(entries)}


LIFE_RANKING = ranking(
    entry("C1", 2, aspect="выставка", about_source=True),
    entry("C2", 2, aspect="быт", outside_period=True),
    entry("C3", 3, aspect="жильё"),
    entry("C4", 1, aspect="жильё"),
    entry("C5", 3, aspect="питание"),
    entry("C6", 3, aspect="работа"),
)


def limits(*, max_facts: int = 20, min_facts: int = 1, domain_cap_floor: int = 0) -> FactLimits:
    return make_limits(
        relevance=True,
        max_facts=max_facts,
        min_facts=min_facts,
        domain_cap_floor=domain_cap_floor,
    )


async def run(
    *replies: object,
    fact_limits: FactLimits | None = None,
    snippets: Sequence[Snippet] = SNIPPETS,
) -> tuple[FactExtraction, ScriptedLLMClient]:
    fake = ScriptedLLMClient(*replies)
    result = await extract_facts(as_client(fake), TOPIC, snippets, fact_limits or limits())
    return result, fake


def extracted(result: FactExtraction) -> FactsExtracted:
    assert isinstance(result, FactsExtracted)
    return result


def texts(result: FactExtraction) -> list[str]:
    return [item.text for item in result.fact_set.facts]


def report(*entries: dict[str, object]) -> RelevanceReport:
    return RelevanceReport.model_validate(ranking(*entries))


async def test_relevant_facts_go_first_and_off_topic_facts_are_set_aside() -> None:
    result, fake = await run(LIFE_REPLY, NO_CONFLICTS, LIFE_RANKING)

    assert texts(result) == [COMMUNAL, CARDS, WEEK, ROOM]
    stats = extracted(result).stats
    assert (stats.relevance_scored, stats.relevance_dropped, stats.relevance_failed) == (6, 0, 0)
    assert (stats.facts_about_source, stats.facts_outside_period) == (1, 1)
    assert (stats.facts_set_aside_off_topic, stats.facts_off_topic_restored) == (2, 0)
    assert len(fake.calls) == 3


async def test_the_short_post_now_takes_relevant_facts() -> None:
    before, _ = await run(LIFE_REPLY, NO_CONFLICTS, fact_limits=make_limits())
    after, _ = await run(LIFE_REPLY, NO_CONFLICTS, LIFE_RANKING)

    assert [item.text for item in select_short_facts(before.fact_set, 3).facts] == [
        EXHIBITION,
        FRIDGE,
        COMMUNAL,
    ]
    assert [item.text for item in select_short_facts(after.fact_set, 3).facts] == [
        COMMUNAL,
        CARDS,
        WEEK,
    ]


async def test_turned_off_it_makes_no_call_and_keeps_the_model_order() -> None:
    result, fake = await run(LIFE_REPLY, NO_CONFLICTS, fact_limits=make_limits())

    assert texts(result) == MODEL_ORDER
    assert len(fake.calls) == 2
    assert extracted(result).stats.relevance_scored == 0


async def test_one_fact_is_not_ranked() -> None:
    result, fake = await run(extraction(fact(WEEK, support("S1", WEEK_QUOTE))))

    assert texts(result) == [WEEK]
    assert len(fake.calls) == 1


async def test_an_invalid_reply_keeps_the_model_order() -> None:
    broken = {"facts": [{"id": "C1", "aspect": "быт"}]}

    result, fake = await run(LIFE_REPLY, NO_CONFLICTS, broken)

    assert texts(result) == MODEL_ORDER
    assert extracted(result).stats.relevance_failed == 1
    assert len(fake.calls) == 3


async def test_a_provider_error_keeps_the_model_order() -> None:
    result, _ = await run(LIFE_REPLY, NO_CONFLICTS, LLMUnavailableError("down"))

    assert texts(result) == MODEL_ORDER
    assert extracted(result).stats.relevance_failed == 1


async def test_a_reply_of_unknown_ids_only_keeps_the_model_order() -> None:
    result, _ = await run(
        LIFE_REPLY, NO_CONFLICTS, ranking(entry("C9", 0), entry("F1", 0), entry("S1", 0))
    )

    assert texts(result) == MODEL_ORDER
    stats = extracted(result).stats
    assert (stats.relevance_scored, stats.relevance_dropped, stats.relevance_failed) == (0, 3, 1)


async def test_unknown_repeated_and_out_of_range_entries_are_dropped() -> None:
    result, _ = await run(
        LIFE_REPLY,
        NO_CONFLICTS,
        ranking(
            entry("C1", 0),
            entry("C1", 3),
            entry("C7", 3),
            entry("C2", 4),
            entry("C3", -1),
            entry("[c6]", 3),
        ),
    )

    stats = extracted(result).stats
    assert (stats.relevance_scored, stats.relevance_dropped, stats.relevance_failed) == (2, 4, 0)
    assert texts(result) == [WEEK, FRIDGE, COMMUNAL, ROOM, CARDS]
    assert stats.facts_unrelated == 1


async def test_a_fact_with_no_claim_is_set_aside_by_relevance_zero() -> None:
    reply = extraction(
        fact(EMPTY, support("S1", EMPTY_QUOTE)),
        fact(COMMUNAL, support("S1", COMMUNAL_QUOTE)),
    )

    result, _ = await run(reply, NO_CONFLICTS, ranking(entry("C1", 0), entry("C2", 3)))

    assert texts(result) == [COMMUNAL]
    assert extracted(result).stats.facts_unrelated == 1


async def test_set_aside_facts_come_back_for_the_floor() -> None:
    result, _ = await run(
        LIFE_REPLY,
        NO_CONFLICTS,
        ranking(*(entry(f"C{index}", 0) for index in range(1, 7))),
        fact_limits=limits(min_facts=3, domain_cap_floor=5),
    )

    outcome = extracted(result)
    assert texts(result) == MODEL_ORDER[:5]
    assert (
        outcome.stats.facts_set_aside_off_topic,
        outcome.stats.facts_off_topic_restored,
    ) == (1, 5)


async def test_ranking_alone_never_makes_the_facts_insufficient() -> None:
    reply = extraction(*(fact(text, support("S1", quote)) for text, quote in THREE_FACTS))

    result, _ = await run(
        reply,
        NO_CONFLICTS,
        ranking(
            entry("C1", 0), entry("C2", 2, about_source=True), entry("C3", 1, outside_period=True)
        ),
        fact_limits=limits(min_facts=3),
    )

    assert isinstance(result, FactsExtracted)
    assert len(result.fact_set.facts) == 3


async def test_the_limit_keeps_relevant_facts_instead_of_the_first_ones() -> None:
    result, _ = await run(
        LIFE_REPLY,
        NO_CONFLICTS,
        ranking(
            entry("C1", 1),
            entry("C2", 1),
            entry("C3", 2),
            entry("C4", 1),
            entry("C5", 3),
            entry("C6", 3),
        ),
        fact_limits=limits(max_facts=3),
    )

    assert texts(result) == [CARDS, WEEK, COMMUNAL]
    assert extracted(result).stats.facts_cut_by_limit == 3


async def test_disputed_and_attributed_facts_are_not_ranked_and_keep_their_place() -> None:
    reply = extraction(
        fact(COMMUNAL, support("S1", COMMUNAL_QUOTE)),
        fact(CARDS, support("S1", CARDS_QUOTE)),
        fact(WEEK, support("S1", WEEK_QUOTE)),
        fact(ROOM, support("S1", ROOM_QUOTE)),
        {
            "stance": "rebutted",
            "text": MYTH,
            "support": [support("S2", MYTH_QUOTE)],
            "rebuttal": {"text": REBUTTAL, "support": [support("S2", REBUTTAL_QUOTE)]},
        },
    )

    result, fake = await run(
        reply,
        conflicts(conflict("C1", "C2")),
        ranking(entry("C1", 3), entry("C2", 3), entry("C3", 0)),
    )

    relevance_prompt = fake.calls[2][0][-1].content
    assert COMMUNAL not in relevance_prompt
    assert CARDS not in relevance_prompt
    assert MYTH not in relevance_prompt
    assert [
        line.split(": ", 1)[1] for line in relevance_prompt.split("Facts:\n")[1].splitlines()
    ] == [WEEK, ROOM, REBUTTAL]
    facts = result.fact_set.facts
    assert [(item.text, item.status, item.stance) for item in facts] == [
        (WEEK, FactStatus.SINGLE, ClaimStance.ASSERTED),
        (ROOM, FactStatus.SINGLE, ClaimStance.ASSERTED),
        (REBUTTAL, FactStatus.SINGLE, ClaimStance.ASSERTED),
        (MYTH, FactStatus.SINGLE, ClaimStance.REBUTTED),
        (COMMUNAL, FactStatus.DISPUTED, ClaimStance.ASSERTED),
        (CARDS, FactStatus.DISPUTED, ClaimStance.ASSERTED),
    ]
    assert facts[3].rebutted_by == [facts[2].id]


async def test_a_rebuttal_set_aside_comes_back_with_its_claim() -> None:
    reply = extraction(
        fact(WEEK, support("S1", WEEK_QUOTE)),
        {
            "stance": "rebutted",
            "text": MYTH,
            "support": [support("S2", MYTH_QUOTE)],
            "rebuttal": {"text": REBUTTAL, "support": [support("S2", REBUTTAL_QUOTE)]},
        },
    )

    result, _ = await run(reply, NO_CONFLICTS, ranking(entry("C1", 3), entry("C2", 0)))

    facts = result.fact_set.facts
    assert [item.text for item in facts] == [WEEK, REBUTTAL, MYTH]
    assert facts[2].rebutted_by == [facts[1].id]
    assert extracted(result).stats.rebuttals_restored == 1


async def test_the_prompt_gives_the_topic_as_a_frame_and_only_ids_and_texts() -> None:
    _, fake = await run(LIFE_REPLY, NO_CONFLICTS, LIFE_RANKING)

    messages, schema, max_tokens = fake.calls[2]
    assert schema is RelevanceReport
    assert max_tokens == RELEVANCE_CHECK_MAX_TOKENS
    assert [message.role for message in messages] == [Role.SYSTEM, Role.USER]
    system, user = messages[0].content, messages[1].content
    assert "it is not a source" in system
    assert "do not use your own knowledge" in system
    assert user.startswith(f"Topic: {TOPIC}\n")
    for position, text in enumerate(MODEL_ORDER, start=1):
        assert f"C{position}: {text}" in user
    assert LIFE_SITE.url not in user
    assert "https://" not in user
    assert "S1" not in user


def test_the_prompt_separates_facts_about_a_source_from_facts_about_the_name() -> None:
    system = render_relevance_check(TOPIC, [("C1", WEEK)])[0].content

    assert "about_source: true only when the fact is about a source" in system
    assert "«На выставке представлены фотографии и плакаты эпохи»" in system
    assert "«Изучение быта этого периода особенно актуально»" in system
    assert "NOT about_source" in system
    assert "«Термин для этого события впервые употребил историк XIX века»" in system
    assert "«Сражение также называют по имени реки»" in system
    assert "«По оценкам историков, войско насчитывало около 30 тысяч человек»" in system
    assert "«Летопись сообщает, что перед походом князь получил благословение»" in system
    assert "«Памятный день сражения отмечают ежегодно»" in system
    assert "«Опровергнуто»" in system
    assert "Direct causes and direct consequences" in system
    assert "A fact dated inside the period is never outside it" in system
    assert "If the topic names no period, outside_period is always false" in system
    assert f"at most {RELEVANCE_MAX_ASPECTS} different aspects" in system


def test_labels_are_matched_like_snippet_labels() -> None:
    check = collect_relevance(report(entry(" [c2] ", 3), entry("C1", 1)), 2)

    assert sorted(check.scores) == [0, 1]
    assert check.scores[1].relevance == 3


def test_an_empty_reply_is_a_failure() -> None:
    check = collect_relevance(report(), 3)

    assert check.failed
    assert check.scores == {}


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        ("  Жильё ", "жильё"),
        ("Рабочее   время", "рабочее время"),
        ("ХОД\tБИТВЫ", "ход битвы"),
        ("", RELEVANCE_OTHER_ASPECT),
        ("   ", RELEVANCE_OTHER_ASPECT),
    ],
)
def test_aspects_are_normalised(aspect: str, expected: str) -> None:
    assert normalize_aspect(aspect) == expected


def test_aspects_beyond_the_cap_merge_into_other() -> None:
    scores = {
        index: FactRelevance(relevance=3, aspect=f"аспект {index}")
        for index in range(RELEVANCE_MAX_ASPECTS + 3)
    }

    capped = cap_aspects(scores)

    assert len({score.aspect for score in capped.values()}) == RELEVANCE_MAX_ASPECTS + 1
    assert [capped[index].aspect for index in range(RELEVANCE_MAX_ASPECTS, len(scores))] == [
        RELEVANCE_OTHER_ASPECT
    ] * 3


def test_a_known_aspect_after_the_cap_keeps_its_name() -> None:
    names = [f"аспект {index}" for index in range(RELEVANCE_MAX_ASPECTS)]
    scores = {index: FactRelevance(aspect=name) for index, name in enumerate(names)}
    scores[len(names)] = FactRelevance(aspect="новый")
    scores[len(names) + 1] = FactRelevance(aspect=names[0])

    capped = cap_aspects(scores)

    assert capped[len(names)].aspect == RELEVANCE_OTHER_ASPECT
    assert capped[len(names) + 1].aspect == names[0]


def test_an_entry_keeps_its_flags() -> None:
    entry_model = RelevanceEntry.model_validate(
        entry("C1", 2, aspect="Выставка", about_source=True, outside_period=True)
    )

    check = collect_relevance(RelevanceReport(facts=[entry_model]), 1)

    assert check.scores[0] == FactRelevance(
        relevance=2, aspect="выставка", about_source=True, outside_period=True
    )
    assert check.scores[0].off_topic


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("Как выглядел обычный день советского человека в 1930-е?", Period(start=1920, end=1949)),
        ("Быт 1930-х годов", Period(start=1920, end=1949)),
        ("Daily life in the 1930s", Period(start=1920, end=1949)),
        ("Битва в 1380 году", Period(start=1370, end=1390)),
        ("Первая мировая война 1914-1918", Period(start=1904, end=1928)),
        ("Куликовская битва", None),
        ("Москва XIV века", None),
        ("Восстание 300 стрельцов", None),
    ],
)
def test_the_period_of_a_topic_is_read_from_its_years(topic: str, expected: Period | None) -> None:
    assert topic_period(topic) == expected


@pytest.mark.parametrize(
    ("text", "outside"),
    [
        ("В 1976 году только две трети семей имели холодильник.", True),
        ("Личный автомобиль был мечтой гражданина 1960-х годов.", True),
        ("Карточную систему отменят уже в 1935 году.", False),
        ("В 1929 году Совнарком утвердил непрерывное производство.", False),
        ("Систему окончательно отменили в 1940 году.", False),
        ("В 1928 г. было 4,6 млн рабочих, в начале 1932 г. уже больше 10 млн.", False),
        ("В 1976 году холодильник был редкостью, а в 1930 году его не было вовсе.", False),
        ("Семьи годами ждали холодильник.", False),
        ("В 1950 году открылся новый завод.", True),
        ("В 1949 году открылся новый завод.", False),
    ],
)
def test_outside_is_confirmed_only_when_every_year_lies_beyond_the_margin(
    text: str, outside: bool
) -> None:
    assert dated_outside(text, Period(start=1920, end=1949)) is outside


def test_without_a_period_nothing_is_dated_outside() -> None:
    assert dated_outside("В 1976 году холодильник был редкостью.", None) is False


def test_an_unconfirmed_outside_flag_is_overruled_and_counted() -> None:
    check = RelevanceCheck(
        scores={
            0: FactRelevance(relevance=3, outside_period=True),
            1: FactRelevance(relevance=3, outside_period=True),
            2: FactRelevance(relevance=3, outside_period=True),
            3: FactRelevance(relevance=3, about_source=True),
        }
    )
    texts = [CARDS, FRIDGE, "Семьи годами ждали холодильник.", EXHIBITION]

    guarded = check_period(check, TOPIC, texts)

    assert [score.outside_period for score in guarded.scores.values()] == [
        False,
        True,
        False,
        False,
    ]
    assert guarded.scores[3].about_source
    assert guarded.period_overruled == 2


async def test_a_topic_without_a_period_never_sets_a_fact_aside_as_outside() -> None:
    fake = ScriptedLLMClient(
        LIFE_REPLY,
        NO_CONFLICTS,
        ranking(*(entry(f"C{index}", 3, outside_period=True) for index in range(1, 7))),
    )

    result = await extract_facts(as_client(fake), "Городской быт", SNIPPETS, limits())

    stats = extracted(result).stats
    assert texts(result) == MODEL_ORDER
    assert (stats.facts_outside_period, stats.relevance_period_overruled) == (0, 6)


async def test_a_date_inside_the_period_keeps_the_fact() -> None:
    result, _ = await run(
        LIFE_REPLY,
        NO_CONFLICTS,
        ranking(*(entry(f"C{index}", 3, outside_period=True) for index in range(1, 7))),
    )

    assert texts(result) == [EXHIBITION, COMMUNAL, ROOM, CARDS, WEEK]
    stats = extracted(result).stats
    assert (stats.facts_outside_period, stats.relevance_period_overruled) == (1, 5)
