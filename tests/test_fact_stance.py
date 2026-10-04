from collections.abc import Sequence

import pytest
from app.config.constants import FACT_CANDIDATES_MAX
from app.config.stance import CLAIM_MARKERS, STRONG_CLAIM_MARKERS
from app.domain.draft import PostFormat, SentenceBudget
from app.domain.fact import (
    ClaimStance,
    FactExtraction,
    FactsExtracted,
    FactStatus,
    InsufficientFacts,
    SourceRef,
)
from app.domain.llm import Role
from app.domain.snippet import Snippet
from app.prompts.writing import assertable_facts, attributed_ids, render_writing
from app.services.facts import FactLimits, extract_facts
from app.services.stance import (
    find_marker,
    parse_stance,
    quote_context,
    reads_as_rebuttal,
    stance_evidence,
    strong_evidence,
)

from fact_helpers import (
    DATE_RU_QUOTE,
    NO_CONFLICTS,
    RU_WIKI,
    conflicts,
    extraction,
    make_limits,
    support,
)
from llm_helpers import ScriptedLLMClient, as_client
from research_helpers import make_snippet

TOPIC = "Трудовой календарь 1930 года"
MYTH_SITE = make_snippet(
    "https://calendar.example.org/reform",
    "В 1929 году в стране ввели новый трудовой календарь. Многие источники пишут, что в "
    "календаре 1930 года было 30 февраля, раз каждый месяц длился 30 дней. Однако "
    "сохранившиеся табели 1930 года показывают обычный февраль из 28 дней. Реформу "
    "свернули в 1931 году.",
)
MYTH_ECHO = make_snippet(
    "https://echo.example.net/note",
    "Принято считать, что в календаре 1930 года было 30 февраля, вспоминает автор заметки.",
)
LEGEND_SITE = make_snippet(
    "https://chronicle.example.com/duel",
    "Войска сошлись утром. По преданию, перед битвой инок Пересвет бился с богатырём "
    "Челубеем. Оба погибли в поединке.",
)
PLAIN_SITE = make_snippet(
    "https://plain.example.org/march",
    "Войско вышло из Коломны в августе. Перед битвой инок Пересвет бился с богатырём "
    "Челубеем. Затем начался бой.",
)
CENSUS_SITE = make_snippet(
    "https://stat.example.org/census",
    "Индустриализация ускорила рост городов. По данным переписи 1939 года городское "
    "население составило 56 млн человек. Деревня при этом сокращалась.",
)
FAR_MARKER_SITE = make_snippet(
    "https://far.example.org/page",
    "По преданию, город основал князь. Стены строили долго. Ворота смотрели на реку. "
    "Крепость выдержала осаду 1521 года.",
)
TAVILY_CHUNKS_SITE = make_snippet(
    "https://chunks.example.org/page",
    "По преданию, здесь стоял монастырь. [...] Крепость выдержала осаду 1521 года.",
)
STANCE_SNIPPETS: list[Snippet] = [MYTH_SITE, MYTH_ECHO, LEGEND_SITE, PLAIN_SITE, CENSUS_SITE]

MYTH_QUOTE = "в календаре 1930 года было 30 февраля"
MYTH_TEXT = "По распространённому утверждению, в календаре 1930 года было 30 февраля."
PLAIN_MYTH_TEXT = "В календаре 1930 года было 30 февраля."
REBUTTAL_QUOTE = "сохранившиеся табели 1930 года показывают обычный февраль из 28 дней"
REBUTTAL_TEXT = "Сохранившиеся табели 1930 года показывают обычный февраль из 28 дней."
REFORM_QUOTE = "В 1929 году в стране ввели новый трудовой календарь"
REFORM_TEXT = "В 1929 году в стране ввели новый трудовой календарь."
DUEL_QUOTE = "перед битвой инок Пересвет бился с богатырём Челубеем"
DUEL_TEXT = "По преданию, перед битвой инок Пересвет бился с богатырём Челубеем."
CENSUS_QUOTE = "городское население составило 56 млн человек"
CENSUS_TEXT = "По данным переписи 1939 года городское население составило 56 млн человек."
CENSUS_SHORT_TEXT = "По данным переписи городское население составило 56 млн человек."
CENSUS_FULL_QUOTE = "По данным переписи 1939 года городское население составило 56 млн человек"
DEATH_QUOTE = "Оба погибли в поединке"
DEATH_TEXT = "Оба бойца погибли в поединке."
GROWTH_QUOTE = "Индустриализация ускорила рост городов"
GROWTH_TEXT = "Индустриализация ускорила рост городов."
SIEGE_QUOTE = "Крепость выдержала осаду 1521 года"


def stance_fact(
    text: str,
    *items: dict[str, str],
    stance: str | None = None,
    rebuttal: dict[str, object] | None = None,
) -> dict[str, object]:
    reply: dict[str, object] = {"text": text, "support": list(items)}
    if stance is not None:
        reply["stance"] = stance
    if rebuttal is not None:
        reply["rebuttal"] = rebuttal
    return reply


def rebuttal_of(text: str, *items: dict[str, str]) -> dict[str, object]:
    return {"text": text, "support": list(items)}


async def run(
    *replies: object,
    limits: FactLimits | None = None,
    snippets: Sequence[Snippet] = STANCE_SNIPPETS,
) -> tuple[FactExtraction, ScriptedLLMClient]:
    fake = ScriptedLLMClient(*replies)
    result = await extract_facts(as_client(fake), TOPIC, snippets, limits or make_limits())
    return result, fake


def extracted(result: FactExtraction) -> FactsExtracted:
    assert isinstance(result, FactsExtracted)
    return result


MYTH_WITH_REBUTTAL = stance_fact(
    MYTH_TEXT,
    support("S1", MYTH_QUOTE),
    stance="rebutted",
    rebuttal=rebuttal_of(REBUTTAL_TEXT, support("S1", REBUTTAL_QUOTE)),
)
REFORM = stance_fact(REFORM_TEXT, support("S1", REFORM_QUOTE))


async def test_a_claim_the_source_rebuts_is_rebutted_and_its_rebuttal_is_a_separate_fact() -> None:
    result, _ = await run(extraction(REFORM, MYTH_WITH_REBUTTAL), NO_CONFLICTS)

    by_text = {fact.text: fact for fact in extracted(result).fact_set.facts}
    myth = by_text[MYTH_TEXT]
    rebuttal = by_text[REBUTTAL_TEXT]
    assert myth.stance is ClaimStance.REBUTTED
    assert myth.status is FactStatus.SINGLE
    assert myth.rebutted_by == [rebuttal.id]
    assert rebuttal.stance is ClaimStance.ASSERTED
    assert rebuttal.rebutted_by == []
    assert by_text[REFORM_TEXT].stance is ClaimStance.ASSERTED


async def test_the_rebutted_claim_never_reaches_the_writer_as_a_fact_to_state() -> None:
    result, _ = await run(extraction(REFORM, MYTH_WITH_REBUTTAL), NO_CONFLICTS)
    fact_set = extracted(result).fact_set

    messages = render_writing(
        fact_set,
        PostFormat.LONG,
        max_chars=25000,
        max_tweets=12,
        budget=SentenceBudget(max_sentences=3, sentence_chars=80),
    )
    [user] = [message.content for message in messages if message.role is Role.USER]
    facts_block, attributed_block = user.split("Attributed claims.")

    myth = next(fact for fact in fact_set.facts if fact.text == MYTH_TEXT)
    assert MYTH_TEXT not in facts_block
    assert REBUTTAL_TEXT in facts_block
    assert MYTH_TEXT in attributed_block
    assert myth.id in attributed_ids(fact_set)
    assert myth not in assertable_facts(fact_set)


async def test_a_claimed_legend_keeps_its_stance_and_its_attribution() -> None:
    result, _ = await run(
        extraction(REFORM, stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance="claimed")),
        NO_CONFLICTS,
    )

    duel = extracted(result).fact_set.facts[-1]
    assert duel.stance is ClaimStance.CLAIMED
    assert duel.text.startswith("По преданию")
    assert result.stats.facts_claimed == 1
    assert result.stats.facts_stance_unmarked == 0


async def test_attributed_facts_do_not_count_towards_the_minimum() -> None:
    result, _ = await run(
        extraction(stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance="claimed")),
        limits=make_limits(min_facts=1),
    )

    assert isinstance(result, InsufficientFacts)
    assert result.assertable_count == 0
    assert [fact.stance for fact in result.fact_set.facts] == [ClaimStance.CLAIMED]


async def test_a_plain_fact_stays_asserted() -> None:
    result, _ = await run(extraction(stance_fact(GROWTH_TEXT, support("S5", GROWTH_QUOTE))))

    [growth] = extracted(result).fact_set.facts
    assert growth.stance is ClaimStance.ASSERTED
    assert result.stats.facts_claimed == 0


async def test_a_fact_without_a_stance_is_asserted() -> None:
    result, _ = await run(extraction(stance_fact(GROWTH_TEXT, support("S5", GROWTH_QUOTE))))

    [growth] = extracted(result).fact_set.facts
    assert growth.stance is ClaimStance.ASSERTED


@pytest.mark.parametrize("value", ["Claimed", " claimed ", "CLAIMED"])
async def test_the_stance_is_read_ignoring_case_and_spaces(value: str) -> None:
    result, _ = await run(
        extraction(
            REFORM,
            stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance=value),
        ),
        NO_CONFLICTS,
    )

    stances = {fact.text: fact.stance for fact in extracted(result).fact_set.facts}
    assert stances[DUEL_TEXT] is ClaimStance.CLAIMED


@pytest.mark.parametrize("value", ["legend", "disputed", "maybe"])
async def test_a_fact_with_an_unknown_stance_is_dropped(value: str) -> None:
    result, _ = await run(
        extraction(REFORM, stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance=value))
    )

    facts = extracted(result).fact_set.facts
    assert [fact.text for fact in facts] == [REFORM_TEXT]
    assert result.stats.facts_unknown_stance == 1
    assert result.stats.facts_verified == 1


async def test_a_claimed_fact_with_no_attribution_near_its_quote_is_lowered_to_asserted() -> None:
    result, _ = await run(
        extraction(stance_fact(DUEL_TEXT, support("S4", DUEL_QUOTE), stance="claimed"))
    )

    [duel] = extracted(result).fact_set.facts
    assert duel.stance is ClaimStance.ASSERTED
    assert result.stats.facts_stance_unmarked == 1
    assert result.stats.facts_claimed == 0


@pytest.mark.parametrize(
    ("text", "quote"),
    [(CENSUS_TEXT, CENSUS_FULL_QUOTE), (CENSUS_SHORT_TEXT, CENSUS_QUOTE)],
)
async def test_a_census_figure_marked_claimed_is_lowered_to_asserted(text: str, quote: str) -> None:
    result, _ = await run(extraction(stance_fact(text, support("S5", quote), stance="claimed")))

    [census] = extracted(result).fact_set.facts
    assert census.stance is ClaimStance.ASSERTED
    assert result.stats.facts_stance_unmarked == 1


async def test_two_sites_repeating_one_myth_do_not_make_it_confirmed() -> None:
    result, _ = await run(
        extraction(
            REFORM,
            stance_fact(
                MYTH_TEXT,
                support("S1", MYTH_QUOTE),
                support("S2", MYTH_QUOTE),
                stance="claimed",
            ),
        ),
        NO_CONFLICTS,
    )

    myth = next(fact for fact in extracted(result).fact_set.facts if fact.text == MYTH_TEXT)
    assert {ref.domain for ref in myth.support} == {"example.org", "example.net"}
    assert myth.stance is ClaimStance.CLAIMED
    assert myth.status is FactStatus.SINGLE


async def test_without_a_stance_the_same_two_sites_would_confirm_the_myth() -> None:
    result, _ = await run(
        extraction(
            REFORM,
            stance_fact(PLAIN_MYTH_TEXT, support("S1", MYTH_QUOTE), support("S2", MYTH_QUOTE)),
        ),
        NO_CONFLICTS,
    )

    myth = next(fact for fact in extracted(result).fact_set.facts if fact.text == PLAIN_MYTH_TEXT)
    assert myth.status is FactStatus.CONFIRMED


async def test_a_claimed_fact_with_a_verified_rebuttal_becomes_rebutted() -> None:
    reply = stance_fact(
        MYTH_TEXT,
        support("S1", MYTH_QUOTE),
        stance="claimed",
        rebuttal=rebuttal_of(REBUTTAL_TEXT, support("S1", REBUTTAL_QUOTE)),
    )
    result, _ = await run(extraction(REFORM, reply), NO_CONFLICTS)

    myth = next(fact for fact in extracted(result).fact_set.facts if fact.text == MYTH_TEXT)
    assert myth.stance is ClaimStance.REBUTTED
    assert len(myth.rebutted_by) == 1


async def test_a_rebuttal_whose_quote_fails_is_dropped_and_the_claim_stays_rebutted() -> None:
    reply = stance_fact(
        MYTH_TEXT,
        support("S1", MYTH_QUOTE),
        stance="rebutted",
        rebuttal=rebuttal_of(REBUTTAL_TEXT, support("S1", "табели показывают тридцатое февраля")),
    )
    result, _ = await run(extraction(REFORM, reply), NO_CONFLICTS)

    facts = extracted(result).fact_set.facts
    assert [fact.text for fact in facts] == [REFORM_TEXT, MYTH_TEXT]
    assert facts[1].stance is ClaimStance.REBUTTED
    assert facts[1].rebutted_by == []
    assert result.stats.rebuttals_proposed == 1
    assert result.stats.rebuttals_verified == 0
    assert result.stats.candidates == 2


async def test_a_rebuttal_with_a_number_missing_from_its_quote_is_dropped() -> None:
    reply = stance_fact(
        MYTH_TEXT,
        support("S1", MYTH_QUOTE),
        stance="rebutted",
        rebuttal=rebuttal_of(
            "Табели 1931 года показывают февраль из 28 дней.", support("S1", REBUTTAL_QUOTE)
        ),
    )
    result, _ = await run(extraction(REFORM, reply), NO_CONFLICTS)

    assert result.stats.facts_number_mismatch == 1
    myth = next(fact for fact in result.fact_set.facts if fact.text == MYTH_TEXT)
    assert myth.rebutted_by == []


async def test_a_rebuttal_cut_by_the_limit_comes_back_with_its_claim() -> None:
    result, _ = await run(
        extraction(REFORM, MYTH_WITH_REBUTTAL),
        NO_CONFLICTS,
        limits=make_limits(max_facts=1),
    )

    facts = extracted(result).fact_set.facts
    assert [fact.text for fact in facts] == [REFORM_TEXT, REBUTTAL_TEXT, MYTH_TEXT]
    assert facts[2].rebutted_by == [facts[1].id]
    assert result.stats.rebuttals_restored == 1


async def test_attributed_facts_are_kept_beyond_the_limit() -> None:
    result, _ = await run(
        extraction(
            REFORM,
            stance_fact(GROWTH_TEXT, support("S5", GROWTH_QUOTE)),
            stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance="claimed"),
        ),
        NO_CONFLICTS,
        limits=make_limits(max_facts=1),
    )

    facts = extracted(result).fact_set.facts
    assert [fact.text for fact in facts] == [REFORM_TEXT, DUEL_TEXT]
    assert result.stats.facts_cut_by_limit == 1


async def test_the_dispute_check_skips_rebutted_claims_and_tags_claimed_ones() -> None:
    result, fake = await run(
        extraction(
            REFORM,
            MYTH_WITH_REBUTTAL,
            stance_fact(DUEL_TEXT, support("S3", DUEL_QUOTE), stance="claimed"),
        ),
        NO_CONFLICTS,
    )

    [user] = [message.content for message in fake.calls[1][0] if message.role is Role.USER]
    assert MYTH_TEXT not in user
    assert f"C1: {REFORM_TEXT}" in user
    assert f"C2: {REBUTTAL_TEXT}" in user
    assert f"C3 [claimed]: {DUEL_TEXT}" in user
    assert not extracted(result).fact_set.disputes


async def test_a_dispute_between_pool_facts_maps_back_to_the_right_facts() -> None:
    result, _ = await run(
        extraction(
            REFORM,
            MYTH_WITH_REBUTTAL,
            stance_fact(GROWTH_TEXT, support("S5", GROWTH_QUOTE)),
        ),
        conflicts({"fact_ids": ["C2", "C3"], "explanation": "Расходятся.", "contradiction": True}),
    )

    fact_set = extracted(result).fact_set
    by_id = {fact.id: fact for fact in fact_set.facts}
    [dispute] = fact_set.disputes
    assert {by_id[fact_id].text for fact_id in dispute.fact_ids} == {REBUTTAL_TEXT, GROWTH_TEXT}
    myth = next(fact for fact in fact_set.facts if fact.text == MYTH_TEXT)
    assert myth.status is FactStatus.SINGLE
    assert myth.rebutted_by == [
        next(fact.id for fact in fact_set.facts if fact.text == REBUTTAL_TEXT)
    ]


async def test_the_extraction_prompt_asks_for_the_stance_and_forbids_guessing_it() -> None:
    _, fake = await run(extraction(REFORM))

    [system, *_] = [message.content for message in fake.calls[0][0] if message.role is Role.SYSTEM]
    assert "Every fact has a stance" in system
    assert "by far the most common stance" in system
    assert "по данным переписи" in system
    assert "Never mark a fact claimed or rebutted because of what you know yourself" in system
    assert "Do not repeat the rebuttal as a separate fact" in system


async def test_a_wikipedia_fact_with_old_style_reply_still_works() -> None:
    result, _ = await run(
        extraction(
            {
                "text": "Куликовская битва произошла 8 сентября 1380 года",
                "support": [support("S1", DATE_RU_QUOTE)],
            }
        ),
        snippets=[RU_WIKI],
    )

    [date] = extracted(result).fact_set.facts
    assert date.stance is ClaimStance.ASSERTED


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ClaimStance.ASSERTED),
        ("asserted", ClaimStance.ASSERTED),
        ("Rebutted", ClaimStance.REBUTTED),
        ("legend", None),
        ("", None),
    ],
)
def test_parse_stance(value: str | None, expected: ClaimStance | None) -> None:
    assert parse_stance(value) is expected


def test_the_context_is_the_quote_sentence_and_its_neighbours() -> None:
    context = quote_context(DUEL_QUOTE, LEGEND_SITE.text)

    assert context == LEGEND_SITE.text


def test_a_marker_two_sentences_away_is_not_in_the_context() -> None:
    context = quote_context(SIEGE_QUOTE, FAR_MARKER_SITE.text)

    assert "предани" not in context
    assert stance_evidence(ClaimStance.CLAIMED, SIEGE_QUOTE, FAR_MARKER_SITE.text) is None


def test_the_context_does_not_cross_a_tavily_chunk_boundary() -> None:
    assert (
        quote_context(SIEGE_QUOTE, TAVILY_CHUNKS_SITE.text) == "Крепость выдержала осаду 1521 года."
    )
    assert stance_evidence(ClaimStance.CLAIMED, SIEGE_QUOTE, TAVILY_CHUNKS_SITE.text) is None


def test_the_context_does_not_cross_a_line_break() -> None:
    text = "По преданию, город основал князь\n\nКрепость выдержала осаду 1521 года."

    assert stance_evidence(ClaimStance.CLAIMED, SIEGE_QUOTE, text) is None


def test_a_quote_not_found_in_sentences_falls_back_to_the_quote_itself() -> None:
    assert quote_context("якобы стоял монастырь здесь", "Совсем другой текст.") == (
        "якобы стоял монастырь здесь"
    )


def test_evidence_names_the_marker_and_the_context() -> None:
    evidence = stance_evidence(ClaimStance.REBUTTED, MYTH_QUOTE, MYTH_SITE.text)

    assert evidence is not None
    assert evidence.marker == "многие источники"
    assert MYTH_QUOTE in evidence.context
    assert "Однако" in evidence.context


def test_a_rebuttal_marker_alone_supports_rebutted_but_not_claimed() -> None:
    text = "Говорили, что мост стоял до 1700 года. Однако мост построили в 1750 году."
    quote = "мост построили в 1750 году"

    assert stance_evidence(ClaimStance.CLAIMED, quote, "Однако мост построили в 1750 году.") is None
    assert (
        stance_evidence(ClaimStance.REBUTTED, quote, "Однако мост построили в 1750 году.")
        is not None
    )
    assert stance_evidence(ClaimStance.CLAIMED, quote, text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Согласно преданию, мост построили в 1750 году.",
        "Мост якобы построили в 1750 году.",
        "Считалось, что мост построили в 1750 году.",
        "According to legend, the bridge was built in 1750.",
        "The bridge was allegedly built in 1750.",
    ],
)
def test_claim_markers_match_inflected_and_english_forms(text: str) -> None:
    assert find_marker(text, CLAIM_MARKERS) is not None


@pytest.mark.parametrize(
    "text",
    [
        "По данным переписи 1939 года городское население составило 56 млн человек.",
        "Согласно указу 1940 года рабочая неделя стала семидневной.",
        "Мост построили в 1750 году.",
    ],
)
def test_plain_sourced_statements_have_no_claim_marker(text: str) -> None:
    assert find_marker(text, CLAIM_MARKERS) is None


DUEL_LEGEND_SITE = make_snippet(
    "https://duel.example.org/legend",
    "Войска выстроились на поле. Предание рассказывает о поединке Пересвета и Челубея перед "
    "битвой. Однако в более раннем источнике Пересвет остался жив и бился в разгар сражения.",
)
CHRONICLE_SITE = make_snippet(
    "https://annals.example.org/march",
    "Согласно летописи, войско вышло из Коломны 20 августа. Шли быстро.",
)
ESTIMATE_SITE = make_snippet(
    "https://army.example.org/size",
    "По оценкам историков, у Мамая было около 80 тысяч воинов. Войско было пёстрым.",
)
LEGENDARY_SITE = make_snippet(
    "https://tanks.example.org/t34",
    "Легендарный танк Т-34 выпускали с 1940 года. Его делали на нескольких заводах.",
)
STRONG_SNIPPETS: list[Snippet] = [
    LEGEND_SITE,
    PLAIN_SITE,
    DUEL_LEGEND_SITE,
    CHRONICLE_SITE,
    ESTIMATE_SITE,
    LEGENDARY_SITE,
    MYTH_SITE,
]
PLAIN_DUEL_TEXT = "Перед битвой инок Пересвет бился с богатырём Челубеем."
LEGEND_DUEL_QUOTE = "Предание рассказывает о поединке Пересвета и Челубея перед битвой"
LEGEND_DUEL_TEXT = "Предание рассказывает о поединке Пересвета и Челубея перед битвой."
ALIVE_QUOTE = "Однако в более раннем источнике Пересвет остался жив"
ALIVE_TEXT = "В более раннем источнике Пересвет остался жив."
CHRONICLE_QUOTE = "Согласно летописи, войско вышло из Коломны 20 августа"
CHRONICLE_TEXT = "Согласно летописи, войско вышло из Коломны 20 августа."
ESTIMATE_QUOTE = "По оценкам историков, у Мамая было около 80 тысяч воинов"
ESTIMATE_TEXT = "По оценкам историков, у Мамая было около 80 тысяч воинов."
TANK_QUOTE = "Легендарный танк Т-34 выпускали с 1940 года"
TANK_TEXT = "Танк Т-34 выпускали с 1940 года."
LINE_UP_QUOTE = "Войска выстроились на поле"
LINE_UP_TEXT = "Войска выстроились на поле."


async def run_strong(*replies: object) -> FactExtraction:
    result, _ = await run(*replies, snippets=STRONG_SNIPPETS)
    return result


def stance_of(result: FactExtraction, text: str) -> ClaimStance:
    return next(fact.stance for fact in result.fact_set.facts if fact.text == text)


async def test_a_plain_quote_merged_with_a_legend_quote_is_raised_to_claimed_and_single() -> None:
    result = await run_strong(
        extraction(
            stance_fact(CHRONICLE_TEXT, support("S4", CHRONICLE_QUOTE)),
            stance_fact(PLAIN_DUEL_TEXT, support("S1", DUEL_QUOTE), support("S2", DUEL_QUOTE)),
        ),
        NO_CONFLICTS,
    )

    duel = next(fact for fact in result.fact_set.facts if fact.text == PLAIN_DUEL_TEXT)
    assert {ref.domain for ref in duel.support} == {"example.com", "example.org"}
    assert duel.stance is ClaimStance.CLAIMED
    assert duel.status is FactStatus.SINGLE
    assert result.stats.facts_stance_upgraded_by_code == 1


async def test_a_plain_quote_alone_is_not_raised() -> None:
    result = await run_strong(
        extraction(stance_fact(PLAIN_DUEL_TEXT, support("S2", DUEL_QUOTE))),
    )

    assert stance_of(result, PLAIN_DUEL_TEXT) is ClaimStance.ASSERTED
    assert result.stats.facts_stance_upgraded_by_code == 0


async def test_a_fact_next_to_a_legend_sentence_is_raised() -> None:
    result = await run_strong(
        extraction(stance_fact(LINE_UP_TEXT, support("S3", LINE_UP_QUOTE))),
    )

    assert stance_of(result, LINE_UP_TEXT) is ClaimStance.CLAIMED


async def test_weak_markers_never_raise_an_asserted_fact() -> None:
    result = await run_strong(
        extraction(
            stance_fact(CHRONICLE_TEXT, support("S4", CHRONICLE_QUOTE)),
            stance_fact(REFORM_TEXT, support("S7", REFORM_QUOTE)),
        ),
        NO_CONFLICTS,
    )

    assert stance_of(result, CHRONICLE_TEXT) is ClaimStance.ASSERTED
    assert stance_of(result, REFORM_TEXT) is ClaimStance.ASSERTED
    assert result.stats.facts_stance_upgraded_by_code == 0


@pytest.mark.parametrize("declared", [None, "claimed"])
async def test_an_estimate_by_historians_stays_asserted(declared: str | None) -> None:
    result = await run_strong(
        extraction(stance_fact(ESTIMATE_TEXT, support("S5", ESTIMATE_QUOTE), stance=declared)),
    )

    assert stance_of(result, ESTIMATE_TEXT) is ClaimStance.ASSERTED
    assert result.stats.facts_stance_upgraded_by_code == 0
    assert result.stats.facts_stance_unmarked == (1 if declared else 0)


async def test_legendary_is_not_a_legend() -> None:
    result = await run_strong(extraction(stance_fact(TANK_TEXT, support("S6", TANK_QUOTE))))

    assert stance_of(result, TANK_TEXT) is ClaimStance.ASSERTED


async def test_a_rebuttal_sentence_next_to_a_legend_is_not_raised() -> None:
    result = await run_strong(
        extraction(
            stance_fact(CHRONICLE_TEXT, support("S4", CHRONICLE_QUOTE)),
            stance_fact(ALIVE_TEXT, support("S3", ALIVE_QUOTE)),
        ),
        NO_CONFLICTS,
    )

    assert stance_of(result, ALIVE_TEXT) is ClaimStance.ASSERTED


async def test_a_rebutted_mark_on_the_rebuttal_itself_is_swapped() -> None:
    swapped = stance_fact(
        ALIVE_TEXT,
        support("S3", ALIVE_QUOTE),
        stance="rebutted",
        rebuttal=rebuttal_of(LEGEND_DUEL_TEXT, support("S3", LEGEND_DUEL_QUOTE)),
    )
    result = await run_strong(
        extraction(stance_fact(CHRONICLE_TEXT, support("S4", CHRONICLE_QUOTE)), swapped),
        NO_CONFLICTS,
    )

    by_text = {fact.text: fact for fact in result.fact_set.facts}
    assert by_text[ALIVE_TEXT].stance is ClaimStance.ASSERTED
    assert by_text[ALIVE_TEXT].rebutted_by == []
    assert by_text[LEGEND_DUEL_TEXT].stance is ClaimStance.CLAIMED
    assert result.stats.facts_stance_role_swapped == 1
    assert result.stats.facts_rebutted == 0
    assert result.stats.rebuttals_verified == 0


async def test_a_rebutted_claim_in_a_sentence_without_an_opener_is_not_swapped() -> None:
    result, _ = await run(extraction(REFORM, MYTH_WITH_REBUTTAL), NO_CONFLICTS)

    assert stance_of(result, MYTH_TEXT) is ClaimStance.REBUTTED
    assert result.stats.facts_stance_role_swapped == 0


def test_an_opener_with_a_strong_marker_in_its_own_sentence_is_not_a_rebuttal() -> None:
    text = "Однако, по преданию, Пересвет погиб первым. Бой продолжался."
    quote = "Пересвет погиб первым"

    assert strong_evidence(quote, text) is not None
    assert not reads_as_rebuttal([source_ref(quote)], {"s": text})


def test_an_opener_without_a_strong_marker_reads_as_a_rebuttal() -> None:
    text = "Предание говорит о гибели обоих. Однако Пересвет остался жив."
    quote = "Пересвет остался жив"

    assert strong_evidence(quote, text) is None
    assert reads_as_rebuttal([source_ref(quote)], {"s": text})


def source_ref(quote: str) -> SourceRef:
    return SourceRef(
        snippet_id="s", url="https://a.example.org/", domain="example.org", quote=quote
    )


@pytest.mark.parametrize(
    "text",
    [
        "Согласно легенде, мост построили за ночь.",
        "Об этом мосте сложено сказание.",
        "Это миф: мост построили позже.",
        "Legend has it that the bridge was built overnight.",
        "The bridge was supposedly built overnight.",
    ],
)
def test_strong_markers_match(text: str) -> None:
    assert find_marker(text, STRONG_CLAIM_MARKERS) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Легендарный танк выпускали с 1940 года.",
        "Мифический зверь украшал герб.",
        "Согласно летописи, войско вышло в августе.",
        "Считается, что мост построили в 1750 году.",
        "По оценкам историков, войско насчитывало 80 тысяч.",
        "The legendary general won the battle.",
        "It follows the Orthodox tradition of the region.",
    ],
)
def test_weak_and_plain_wordings_are_not_strong_markers(text: str) -> None:
    assert find_marker(text, STRONG_CLAIM_MARKERS) is None


async def test_the_prompt_bounds_the_candidates_at_the_start_and_the_end() -> None:
    _, fake = await run(extraction(REFORM))

    [system, *_] = [message.content for message in fake.calls[0][0] if message.role is Role.SYSTEM]
    bound = f"Return at most {FACT_CANDIDATES_MAX} facts"
    assert system.index(bound) < system.index("Rules:")
    assert system.rindex(f"never more than {FACT_CANDIDATES_MAX}") > system.index("9.")
    assert "leave it out for an asserted one" in system


BRIDGE_CLAIM_SITE = make_snippet(
    "https://bridges.example.org/river",
    "Город рос быстро. Таким образом, мост через реку существовал в 1900 году. Торговля шла бойко.",
)
BRIDGE_DENIAL_SITE = make_snippet(
    "https://archive.example.net/river",
    "Переправа была паромной. Моста через реку в 1900 году не было. Его построили позже.",
)
TOWER_SITE = make_snippet(
    "https://tower.example.com/gate",
    "По преданию, у городских ворот стояла сторожевая башня.",
)
BRIDGE_SNIPPETS: list[Snippet] = [BRIDGE_CLAIM_SITE, BRIDGE_DENIAL_SITE, TOWER_SITE]
BRIDGE_CLAIM_TEXT = "По утверждению источника, мост через реку существовал в 1900 году."
BRIDGE_CLAIM_QUOTE = "мост через реку существовал в 1900 году"
BRIDGE_DENIAL_TEXT = "Моста через реку в 1900 году не было."
BRIDGE_DENIAL_QUOTE = "Моста через реку в 1900 году не было"
TOWER_TEXT = "По преданию, у городских ворот стояла сторожевая башня."
TOWER_QUOTE = "у городских ворот стояла сторожевая башня"


async def test_an_attributed_claim_and_its_denial_are_both_disputed() -> None:
    result, fake = await run(
        extraction(
            stance_fact(BRIDGE_CLAIM_TEXT, support("S1", BRIDGE_CLAIM_QUOTE)),
            stance_fact(BRIDGE_DENIAL_TEXT, support("S2", BRIDGE_DENIAL_QUOTE)),
            stance_fact(TOWER_TEXT, support("S3", TOWER_QUOTE), stance="claimed"),
        ),
        conflicts(
            {
                "fact_ids": ["C1", "C2"],
                "explanation": "Одно говорит, что мост был, другое, что его не было.",
                "contradiction": True,
            }
        ),
        snippets=BRIDGE_SNIPPETS,
    )

    [system, user] = [message.content for message in fake.calls[1][0]][-2:]
    assert "the attribution does not remove the contradiction" in system
    assert "Compare the lines marked [claimed] with the plain facts and with the denials" in system
    assert f"C1: {BRIDGE_CLAIM_TEXT}" in user
    assert f"C2: {BRIDGE_DENIAL_TEXT}" in user
    assert f"C3 [claimed]: {TOWER_TEXT}" in user

    by_text = {fact.text: fact for fact in result.fact_set.facts}
    assert by_text[BRIDGE_CLAIM_TEXT].stance is ClaimStance.ASSERTED
    assert by_text[BRIDGE_CLAIM_TEXT].status is FactStatus.DISPUTED
    assert by_text[BRIDGE_DENIAL_TEXT].status is FactStatus.DISPUTED
    [dispute] = result.fact_set.disputes
    assert {by_text[BRIDGE_CLAIM_TEXT].id, by_text[BRIDGE_DENIAL_TEXT].id} == set(dispute.fact_ids)
    assert by_text[TOWER_TEXT].status is FactStatus.SINGLE
