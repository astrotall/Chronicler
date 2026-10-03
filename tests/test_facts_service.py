import logging
from collections.abc import Sequence

import pytest
from app.config.constants import (
    DISPUTE_CHECK_MAX_TOKENS,
    FACT_CANDIDATES_MAX,
    FACT_EXTRACTION_MAX_TOKENS,
)
from app.domain.fact import (
    ExtractionOutcome,
    ExtractionStats,
    FactExtraction,
    FactSet,
    FactsExtracted,
    FactStatus,
    InsufficientFacts,
)
from app.domain.llm import Role
from app.domain.snippet import Snippet
from app.llm.errors import LLMInvalidResponseError, LLMUnavailableError
from app.services.facts import (
    ConflictReport,
    ExtractedFacts,
    FactLimits,
    cut_at_word,
    extract_facts,
    fit_to_budget,
)
from app.services.quote_check import QuoteCheck, check_quote

from fact_helpers import (
    ARMY_60_QUOTE,
    ARMY_150_QUOTE,
    DATE_EN_QUOTE,
    DATE_RU_QUOTE,
    DATE_SITE_QUOTE,
    HISTORY_SITE,
    NEWS_SITE,
    NO_CONFLICTS,
    RU_WIKI,
    SNIPPETS,
    WINNER_QUOTE,
    ScriptedLLMClient,
    as_client,
    conflict,
    conflicts,
    extraction,
    fact,
    make_limits,
    support,
)
from research_helpers import make_settings, make_snippet

TOPIC = "Куликовская битва"
DATE_FACT = "Куликовская битва произошла 8 сентября 1380 года"
WINNER_FACT = "Войско Дмитрия Донского разбило войско Мамая"
ARMY_60_FACT = "Русское войско насчитывало 60 000 человек"
ARMY_150_FACT = "Русское войско насчитывало 150 000 человек"
PLACE_FACT = "Сражение произошло у впадения Непрядвы в Дон"
PLACE_QUOTE = "у впадения Непрядвы в Дон"


async def run(
    *replies: object,
    limits: FactLimits | None = None,
    snippets: Sequence[Snippet] = SNIPPETS,
) -> tuple[FactExtraction, ScriptedLLMClient]:
    fake = ScriptedLLMClient(*replies)
    result = await extract_facts(as_client(fake), TOPIC, snippets, limits or make_limits())
    return result, fake


def extracted(result: FactExtraction) -> FactsExtracted:
    assert isinstance(result, FactsExtracted)
    return result


def texts(fact_set: FactSet) -> list[str]:
    return [item.text for item in fact_set.facts]


async def test_fact_from_one_wikipedia_snippet_is_single() -> None:
    result, fake = await run(extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE))))

    facts = extracted(result).fact_set.facts
    assert [(item.id, item.status) for item in facts] == [("F1", FactStatus.SINGLE)]
    assert facts[0].support[0].snippet_id == RU_WIKI.id
    assert facts[0].support[0].url == RU_WIKI.url
    assert facts[0].support[0].domain == "wikipedia.org"
    assert facts[0].support[0].quote == DATE_RU_QUOTE
    assert len(fake.calls) == 1


async def test_two_independent_domains_make_a_fact_confirmed() -> None:
    result, _ = await run(
        extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S3", DATE_SITE_QUOTE)))
    )

    [item] = extracted(result).fact_set.facts
    assert item.status is FactStatus.CONFIRMED
    assert {ref.domain for ref in item.support} == {"wikipedia.org", "example.org"}


async def test_russian_and_english_wikipedia_are_one_domain() -> None:
    result, _ = await run(
        extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S2", DATE_EN_QUOTE)))
    )

    [item] = extracted(result).fact_set.facts
    assert item.status is FactStatus.SINGLE
    assert len(item.support) == 2


async def test_subdomains_of_one_site_are_one_domain() -> None:
    result, _ = await run(
        extraction(fact(DATE_FACT, support("S3", DATE_SITE_QUOTE), support("S4", DATE_SITE_QUOTE)))
    )

    [item] = extracted(result).fact_set.facts
    assert item.status is FactStatus.SINGLE
    assert [ref.snippet_id for ref in item.support] == [HISTORY_SITE.id, NEWS_SITE.id]


async def test_unknown_snippet_alias_drops_the_support() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S9", DATE_SITE_QUOTE)),
            fact(WINNER_FACT, support(RU_WIKI.id, WINNER_QUOTE)),
        )
    )

    outcome = extracted(result)
    assert texts(outcome.fact_set) == [DATE_FACT]
    assert [ref.snippet_id for ref in outcome.fact_set.facts[0].support] == [RU_WIKI.id]
    assert outcome.stats.support_unknown_snippet == 2
    assert outcome.stats.facts_unsupported == 1


@pytest.mark.parametrize("alias", ["S1", "s1", " S1 ", "[S1]"])
async def test_alias_tolerates_case_spaces_and_brackets(alias: str) -> None:
    result, _ = await run(extraction(fact(DATE_FACT, support(alias, DATE_RU_QUOTE))))

    assert texts(extracted(result).fact_set) == [DATE_FACT]


async def test_quote_from_another_snippet_than_named_is_dropped() -> None:
    result, _ = await run(extraction(fact(DATE_FACT, support("S2", DATE_RU_QUOTE))))

    assert isinstance(result, InsufficientFacts)
    assert result.fact_set.facts == []
    assert result.stats.support_not_found == 1
    assert result.stats.facts_unsupported == 1


async def test_only_verified_support_is_kept() -> None:
    result, _ = await run(
        extraction(
            fact(
                DATE_FACT,
                support("S1", DATE_RU_QUOTE),
                support("S3", "Сражение состоялось 8 сентября 1380 года при Непрядве"),
                support("S2", "8 September 1380"),
                support("S1", DATE_RU_QUOTE + "."),
            )
        )
    )

    outcome = extracted(result)
    [item] = outcome.fact_set.facts
    assert [ref.snippet_id for ref in item.support] == [RU_WIKI.id]
    assert item.status is FactStatus.SINGLE
    stats = outcome.stats
    assert (stats.support_proposed, stats.support_not_found, stats.support_too_short) == (4, 1, 1)
    assert stats.support_duplicate == 1
    assert stats.support_dropped == 2


async def test_fact_without_verified_support_is_dropped_entirely() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(WINNER_FACT, support("S1", "Дмитрий Донской разбил войско Мамая")),
            fact(PLACE_FACT),
        )
    )

    outcome = extracted(result)
    assert texts(outcome.fact_set) == [DATE_FACT]
    assert outcome.stats.facts_unsupported == 2
    for item in outcome.fact_set.facts:
        assert item.support
        for ref in item.support:
            snippet = next(s for s in SNIPPETS if s.id == ref.snippet_id)
            assert check_quote(ref.quote, snippet.text, 20) is QuoteCheck.MATCH


async def test_fact_with_a_number_missing_from_its_quotes_is_dropped() -> None:
    result, _ = await run(
        extraction(
            fact("Куликовская битва произошла 8 сентября 1381 года", support("S1", DATE_RU_QUOTE)),
            fact(ARMY_150_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
        )
    )

    outcome = extracted(result)
    assert texts(outcome.fact_set) == [ARMY_60_FACT]
    assert outcome.stats.facts_number_mismatch == 2
    assert outcome.stats.support_number_mismatch == 2
    assert outcome.stats.support_dropped == 2


async def test_english_quote_supports_a_russian_fact_with_the_same_number() -> None:
    result, _ = await run(extraction(fact(DATE_FACT, support("S2", DATE_EN_QUOTE))))

    assert texts(extracted(result).fact_set) == [DATE_FACT]


async def test_contradicting_facts_are_disputed_with_an_explanation() -> None:
    result, fake = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_150_FACT, support("S5", ARMY_150_QUOTE)),
        ),
        conflicts(conflict("C2", "C3", explanation="Оценки численности войска расходятся")),
    )

    outcome = extracted(result)
    statuses = {item.text: (item.id, item.status) for item in outcome.fact_set.facts}
    assert statuses == {
        DATE_FACT: ("F1", FactStatus.SINGLE),
        ARMY_60_FACT: ("F2", FactStatus.DISPUTED),
        ARMY_150_FACT: ("F3", FactStatus.DISPUTED),
    }
    [dispute] = outcome.fact_set.disputes
    assert dispute.fact_ids == ["F2", "F3"]
    assert dispute.explanation == "Оценки численности войска расходятся"
    assert outcome.stats.disputes == 1
    assert outcome.stats.facts_disputed == 2
    messages, schema, max_tokens = fake.calls[1]
    assert schema is ConflictReport
    assert max_tokens == DISPUTE_CHECK_MAX_TOKENS
    user_text = messages[-1].content
    assert f"C1: {DATE_FACT}" in user_text
    assert ARMY_60_QUOTE not in user_text
    assert RU_WIKI.url not in user_text


async def test_dispute_check_sees_only_verified_facts() -> None:
    _, fake = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(PLACE_FACT, support("S9", PLACE_QUOTE)),
            fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
        ),
        NO_CONFLICTS,
    )

    user_text = fake.calls[1][0][-1].content
    assert user_text.count("\n") == 2
    assert f"C2: {WINNER_FACT}" in user_text
    assert PLACE_FACT not in user_text


async def test_disputed_overrides_confirmed() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S3", DATE_SITE_QUOTE)),
            fact("Сражение состоялось 8 сентября 1380 года", support("S4", DATE_SITE_QUOTE)),
        ),
        conflicts(conflict("C1", "C2")),
    )

    [first, second] = result.fact_set.facts
    assert first.text == DATE_FACT
    assert {ref.domain for ref in first.support} == {"wikipedia.org", "example.org"}
    assert (first.status, second.status) == (FactStatus.DISPUTED, FactStatus.DISPUTED)


async def test_unknown_ids_in_the_dispute_reply_are_ignored() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_150_FACT, support("S5", ARMY_150_QUOTE)),
        ),
        conflicts(
            conflict("C2", "c3", "C7", "F1", "C2"),
            conflict("C1", "C99"),
            conflict("X", "Y"),
        ),
    )

    outcome = extracted(result)
    assert [dispute.fact_ids for dispute in outcome.fact_set.disputes] == [["F2", "F3"]]
    assert outcome.fact_set.facts[0].status is FactStatus.SINGLE
    assert outcome.stats.dispute_unknown_ids == 5
    assert outcome.stats.disputes == 1


async def test_dispute_check_is_skipped_for_fewer_than_two_facts() -> None:
    result, fake = await run(extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE))))

    assert isinstance(result, FactsExtracted)
    assert len(fake.calls) == 1


async def test_llm_error_in_the_dispute_check_propagates() -> None:
    error = LLMUnavailableError("down")
    two_facts = extraction(
        fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
        fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
    )

    with pytest.raises(LLMUnavailableError) as raised:
        await run(two_facts, error)

    assert raised.value is error


async def test_invalid_dispute_reply_propagates() -> None:
    two_facts = extraction(
        fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
        fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
    )

    with pytest.raises(LLMInvalidResponseError):
        await run(
            two_facts,
            {"conflicts": [{"fact_ids": ["C1", "C2"], "explanation": "", "contradiction": True}]},
        )


async def test_dispute_reply_without_a_verdict_is_invalid() -> None:
    two_facts = extraction(
        fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
        fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
    )

    with pytest.raises(LLMInvalidResponseError):
        await run(two_facts, {"conflicts": [{"fact_ids": ["C1", "C2"], "explanation": "x"}]})


async def test_group_the_model_marks_as_no_contradiction_is_withdrawn() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_150_FACT, support("S5", ARMY_150_QUOTE)),
        ),
        conflicts(
            conflict("C1", "C2", explanation="Это не противоречие", contradiction=False),
            conflict("C2", "C3"),
        ),
    )

    outcome = extracted(result)
    assert [item.status for item in outcome.fact_set.facts] == [
        FactStatus.SINGLE,
        FactStatus.DISPUTED,
        FactStatus.DISPUTED,
    ]
    assert [dispute.fact_ids for dispute in outcome.fact_set.disputes] == [["F2", "F3"]]
    assert (outcome.stats.disputes, outcome.stats.disputes_withdrawn) == (1, 1)


@pytest.mark.parametrize(
    "reply",
    [
        {"facts": "not a list"},
        {"facts": [{"text": "", "support": []}]},
        {"facts": [{"text": DATE_FACT, "support": [{"snippet": "S1"}]}]},
        {"facts": [{"text": DATE_FACT}]},
        {},
    ],
)
async def test_invalid_extraction_reply_propagates(reply: object) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await run(reply)


async def test_llm_error_in_extraction_propagates() -> None:
    with pytest.raises(LLMUnavailableError):
        await run(LLMUnavailableError("down"))


async def test_limit_prefers_confirmed_over_single() -> None:
    result, _ = await run(
        extraction(
            fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S3", DATE_SITE_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(
                "Сражение состоялось 8 сентября 1380 года",
                support("S4", DATE_SITE_QUOTE),
                support("S2", DATE_EN_QUOTE),
            ),
        ),
        NO_CONFLICTS,
        limits=make_limits(max_facts=3),
    )

    outcome = extracted(result)
    assert [(item.id, item.text, item.status) for item in outcome.fact_set.facts] == [
        ("F1", DATE_FACT, FactStatus.CONFIRMED),
        ("F2", "Сражение состоялось 8 сентября 1380 года", FactStatus.CONFIRMED),
        ("F3", WINNER_FACT, FactStatus.SINGLE),
    ]
    assert outcome.stats.facts_cut_by_limit == 1
    assert outcome.stats.facts_kept == 3


async def test_disputed_facts_are_kept_beyond_the_limit() -> None:
    result, fake = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S3", DATE_SITE_QUOTE)),
            fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_150_FACT, support("S5", ARMY_150_QUOTE)),
        ),
        conflicts(conflict("C3", "C4")),
        limits=make_limits(max_facts=1),
    )

    outcome = extracted(result)
    assert [(item.id, item.text, item.status) for item in outcome.fact_set.facts] == [
        ("F1", DATE_FACT, FactStatus.CONFIRMED),
        ("F2", ARMY_60_FACT, FactStatus.DISPUTED),
        ("F3", ARMY_150_FACT, FactStatus.DISPUTED),
    ]
    assert outcome.fact_set.disputes[0].fact_ids == ["F2", "F3"]
    assert outcome.stats.facts_cut_by_limit == 1
    user_text = fake.calls[1][0][-1].content
    assert f"C4: {ARMY_150_FACT}" in user_text


async def test_no_facts_is_insufficient() -> None:
    result, fake = await run(extraction(), limits=make_limits(min_facts=3))

    assert isinstance(result, InsufficientFacts)
    assert result.outcome is ExtractionOutcome.INSUFFICIENT
    assert (result.assertable_count, result.required) == (0, 3)
    assert result.fact_set.facts == []
    assert len(fake.calls) == 1


async def test_fewer_facts_than_the_minimum_is_insufficient() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
        ),
        NO_CONFLICTS,
        limits=make_limits(min_facts=3),
    )

    assert isinstance(result, InsufficientFacts)
    assert result.assertable_count == 2
    assert texts(result.fact_set) == [DATE_FACT, WINNER_FACT]


async def test_exactly_the_minimum_is_enough() -> None:
    result, _ = await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE)),
            fact(WINNER_FACT, support("S1", WINNER_QUOTE)),
        ),
        NO_CONFLICTS,
        limits=make_limits(min_facts=2),
    )

    assert extracted(result).outcome is ExtractionOutcome.EXTRACTED


async def test_disputed_facts_do_not_count_towards_the_minimum() -> None:
    result, _ = await run(
        extraction(
            fact(ARMY_60_FACT, support("S3", ARMY_60_QUOTE)),
            fact(ARMY_150_FACT, support("S5", ARMY_150_QUOTE)),
        ),
        conflicts(conflict("C1", "C2")),
        limits=make_limits(min_facts=1),
    )

    assert isinstance(result, InsufficientFacts)
    assert result.assertable_count == 0
    assert [item.status for item in result.fact_set.facts] == [FactStatus.DISPUTED] * 2
    assert len(result.fact_set.disputes) == 1


async def test_no_snippets_is_insufficient_without_calling_the_model() -> None:
    result, fake = await run(snippets=[])

    assert isinstance(result, InsufficientFacts)
    assert result.stats == ExtractionStats()
    assert fake.calls == []


async def test_blank_topic_is_rejected() -> None:
    with pytest.raises(ValueError, match="topic"):
        await extract_facts(as_client(ScriptedLLMClient()), "  ", SNIPPETS, make_limits())


async def test_prompt_shows_aliases_titles_and_texts_but_not_ids() -> None:
    _, fake = await run(extraction())

    messages, schema, max_tokens = fake.calls[0]
    assert schema is ExtractedFacts
    assert max_tokens == FACT_EXTRACTION_MAX_TOKENS
    assert [message.role for message in messages] == [Role.SYSTEM, Role.USER]
    user_text = messages[-1].content
    assert TOPIC in user_text
    for position, snippet in enumerate(SNIPPETS, start=1):
        assert f"[S{position}] {snippet.title}\n{snippet.text}" in user_text
        assert snippet.id not in user_text


async def test_logs_hold_counters_but_no_texts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app.services.facts")

    await run(
        extraction(
            fact(DATE_FACT, support("S1", DATE_RU_QUOTE), support("S9", "secret invented quote")),
            fact(WINNER_FACT, support("S1", "Дмитрий Донской разбил войско Мамая")),
        )
    )

    assert "support_unknown_snippet=1" in caplog.text
    assert "support_not_found=1" in caplog.text
    assert RU_WIKI.id in caplog.text
    for secret in (DATE_FACT, DATE_RU_QUOTE, "secret invented quote", "Дмитрий", "S9"):
        assert secret not in caplog.text


def test_snippets_under_the_budget_are_unchanged() -> None:
    fitted, truncated = fit_to_budget(SNIPPETS, 60000)

    assert fitted == SNIPPETS
    assert truncated == 0


def test_over_the_budget_long_snippets_are_cut_and_short_ones_kept() -> None:
    long_text = " ".join(["слово"] * 400)
    long_one = make_snippet("https://ru.wikipedia.org/wiki/Long", long_text)
    short_one = make_snippet("https://example.org/short", "короткий текст")
    budget = 300

    fitted, truncated = fit_to_budget([long_one, short_one], budget)

    assert truncated == 1
    assert fitted[1] == short_one
    assert fitted[0].id == long_one.id
    assert long_text.startswith(fitted[0].text)
    assert fitted[0].text.endswith("слово")
    assert sum(len(snippet.text) for snippet in fitted) <= budget


def test_budget_is_shared_evenly_between_long_snippets() -> None:
    first = make_snippet("https://a.org/1", "а" * 1000)
    second = make_snippet("https://b.org/2", "б" * 3000)

    fitted, truncated = fit_to_budget([first, second], 1000)

    assert truncated == 2
    assert [len(snippet.text) for snippet in fitted] == [500, 500]


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("один два три", 20, "один два три"),
        ("один два три", 7, "один"),
        ("один два три", 8, "один два"),
        ("один два три", 9, "один два"),
        ("один два три", 3, "оди"),
        ("один два три", 5, "один"),
    ],
)
def test_cut_at_word(text: str, limit: int, expected: str) -> None:
    assert cut_at_word(text, limit) == expected


async def test_quote_from_the_part_cut_by_the_budget_is_not_found() -> None:
    long_text = "Начало статьи. " + " ".join(["наполнитель"] * 200) + " " + DATE_RU_QUOTE
    long_one = make_snippet("https://ru.wikipedia.org/wiki/Long", long_text)
    fake = ScriptedLLMClient(extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE))))

    result = await extract_facts(
        as_client(fake), TOPIC, [long_one], make_limits(input_max_chars=500)
    )

    assert isinstance(result, InsufficientFacts)
    assert result.stats.snippets_truncated == 1
    assert result.stats.support_not_found == 1
    assert DATE_RU_QUOTE not in fake.calls[0][0][-1].content


def test_limits_from_settings() -> None:
    limits = FactLimits.from_settings(make_settings())

    assert limits == FactLimits(
        input_max_chars=60000,
        min_quote_chars=20,
        max_facts=20,
        min_facts=3,
        domain_groups=(("wikipedia.org", "wikimedia.org", "ruwiki.ru", "wikiwand.com"),),
    )


async def test_candidates_over_the_cap_are_dropped_not_rejected() -> None:
    many = [fact(WINNER_FACT, support("S1", WINNER_QUOTE)) for _ in range(45)]

    result, _ = await run(
        extraction(fact(DATE_FACT, support("S1", DATE_RU_QUOTE)), *many), NO_CONFLICTS
    )

    stats = extracted(result).stats
    assert (stats.candidates, stats.candidates_over_limit) == (FACT_CANDIDATES_MAX, 6)
    assert stats.facts_verified == FACT_CANDIDATES_MAX
