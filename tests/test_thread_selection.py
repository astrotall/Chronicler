import logging
import re

import pytest
from app.config.constants import THREAD_DEFAULT_MAX_ATTRIBUTED, THREAD_DEFAULT_MAX_FACTS
from app.domain.draft import Draft, DraftPart, PostFormat
from app.domain.fact import ClaimStance, Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Message, Role
from app.services.generator import (
    WritingLimits,
    facts_for_prompt,
    thread_size,
    write_draft,
)
from app.services.short_post import select_short_facts
from app.services.style_review import review_style
from app.services.thread_selection import select_thread_facts
from pydantic import ValidationError

from llm_helpers import ScriptedLLMClient, as_client
from style_helpers import NO_FINDINGS, finding, findings, make_style_limits

SOURCE_URL = "https://unique-selection-source.example.org/day"
CAP = 10
ALLOWANCE = 2
FACT_ID = re.compile(r"^(F\d+)\b", re.MULTILINE)


def make_fact(
    fact_id: str,
    text: str | None = None,
    *,
    status: FactStatus = FactStatus.SINGLE,
    stance: ClaimStance = ClaimStance.ASSERTED,
    rebutted_by: tuple[str, ...] = (),
) -> Fact:
    body = text or f"Факт номер {fact_id}."
    return Fact(
        id=fact_id,
        text=body,
        support=[SourceRef(snippet_id="abc", url=SOURCE_URL, domain="example.org", quote=body)],
        status=status,
        stance=stance,
        rebutted_by=list(rebutted_by),
    )


def asserted(count: int, start: int = 1) -> list[Fact]:
    return [make_fact(f"F{index}") for index in range(start, start + count)]


def claimed(fact_id: str) -> Fact:
    return make_fact(fact_id, stance=ClaimStance.CLAIMED)


def disputed(fact_id: str, text: str) -> Fact:
    return make_fact(fact_id, text, status=FactStatus.DISPUTED)


def fact_set_of(facts: list[Fact], disputes: list[Dispute] | None = None) -> FactSet:
    return FactSet(topic="Обычный день", facts=facts, disputes=disputes or [])


def ids(fact_set: FactSet) -> list[str]:
    return [fact.id for fact in fact_set.facts]


ARRIVAL_DISPUTE = Dispute(fact_ids=["F30", "F31"], explanation="Даты прибытия расходятся.")


def mixed_set() -> FactSet:
    return fact_set_of(
        [
            *asserted(12),
            claimed("F20"),
            disputed("F30", "Мамай подошёл 8 сентября."),
            disputed("F31", "Мамай подошёл 7 сентября."),
            claimed("F21"),
            claimed("F22"),
        ],
        [ARRIVAL_DISPUTE],
    )


def test_the_highest_priority_asserted_facts_are_kept_up_to_the_cap() -> None:
    selected = select_thread_facts(fact_set_of(asserted(24)), CAP, ALLOWANCE)

    assert ids(selected) == [f"F{index}" for index in range(1, CAP + 1)]
    assert selected.topic == "Обычный день"


def test_the_selection_is_a_subset_in_the_original_order_with_the_same_facts() -> None:
    original = mixed_set()

    selected = select_thread_facts(original, CAP, ALLOWANCE)

    by_id = {fact.id: fact for fact in original.facts}
    positions = [ids(original).index(fact_id) for fact_id in ids(selected)]
    assert positions == sorted(positions)
    assert all(fact == by_id[fact.id] for fact in selected.facts)


def test_a_set_under_the_cap_keeps_all_asserted_facts() -> None:
    selected = select_thread_facts(fact_set_of(asserted(7)), CAP, ALLOWANCE)

    assert ids(selected) == [f"F{index}" for index in range(1, 8)]


def test_one_claimed_fact_and_one_disputed_group_are_kept_and_a_third_unit_is_left_out() -> None:
    selected = select_thread_facts(mixed_set(), CAP, ALLOWANCE)

    assert ids(selected) == [*(f"F{index}" for index in range(1, CAP + 1)), "F20", "F30", "F31"]
    assert selected.disputes == [ARRIVAL_DISPUTE]
    assert "F21" not in ids(selected)
    assert "F22" not in ids(selected)


def test_units_are_taken_in_the_order_of_their_first_member() -> None:
    reordered = fact_set_of(
        [
            *asserted(12),
            disputed("F30", "Мамай подошёл 8 сентября."),
            disputed("F31", "Мамай подошёл 7 сентября."),
            claimed("F20"),
            claimed("F21"),
        ],
        [ARRIVAL_DISPUTE],
    )

    selected = select_thread_facts(reordered, CAP, ALLOWANCE)

    assert ids(selected)[CAP:] == ["F30", "F31", "F20"]


def test_a_disputed_group_is_never_split_by_the_allowance() -> None:
    selected = select_thread_facts(mixed_set(), CAP, 2)
    assert {"F30", "F31"} <= set(ids(selected))

    one_unit = select_thread_facts(mixed_set(), CAP, 1)
    assert ids(one_unit)[CAP:] == ["F20"]

    group_first = fact_set_of(
        [
            *asserted(12),
            disputed("F30", "Мамай подошёл 8 сентября."),
            disputed("F31", "Мамай подошёл 7 сентября."),
            claimed("F20"),
        ],
        [ARRIVAL_DISPUTE],
    )
    assert ids(select_thread_facts(group_first, CAP, 1))[CAP:] == ["F30", "F31"]


def test_overlapping_dispute_groups_count_as_one_unit() -> None:
    overlapping = fact_set_of(
        [
            *asserted(12),
            disputed("F30", "Первая версия 100."),
            disputed("F31", "Вторая версия 200."),
            disputed("F32", "Третья версия 300."),
            claimed("F20"),
        ],
        [
            Dispute(fact_ids=["F30", "F31"], explanation="Версии расходятся."),
            Dispute(fact_ids=["F31", "F32"], explanation="Версии расходятся."),
        ],
    )

    selected = select_thread_facts(overlapping, CAP, 1)

    assert ids(selected)[CAP:] == ["F30", "F31", "F32"]


def rebutted_set(rebuttal_among_asserted_first: bool) -> FactSet:
    rebuttal = "F3" if rebuttal_among_asserted_first else "F12"
    return fact_set_of(
        [
            *asserted(12),
            make_fact("F40", stance=ClaimStance.REBUTTED, rebutted_by=(rebuttal,)),
            claimed("F20"),
        ]
    )


def test_a_rebutted_fact_is_added_together_with_a_rebuttal_that_the_cap_cut() -> None:
    selected = select_thread_facts(rebutted_set(False), CAP, 1)

    assert ids(selected) == [*(f"F{index}" for index in range(1, CAP + 1)), "F12", "F40"]


def test_a_rebutted_fact_is_added_alone_when_its_rebuttal_is_already_offered() -> None:
    selected = select_thread_facts(rebutted_set(True), CAP, 1)

    assert ids(selected) == [*(f"F{index}" for index in range(1, CAP + 1)), "F40"]


def test_a_rebutted_pair_is_added_whole_or_not_at_all() -> None:
    squeezed = fact_set_of(
        [
            *asserted(10),
            claimed("F20"),
            claimed("F21"),
            *asserted(2, 11),
            make_fact("F40", stance=ClaimStance.REBUTTED, rebutted_by=("F12",)),
        ]
    )

    selected = select_thread_facts(squeezed, CAP, 2)

    assert ids(selected)[CAP:] == ["F20", "F21"]
    assert "F12" not in ids(selected)
    assert "F40" not in ids(selected)
    assert ids(select_thread_facts(squeezed, CAP, 3))[CAP:] == ["F20", "F21", "F12", "F40"]


def test_a_rebutted_fact_with_no_rebuttal_to_offer_is_not_offered() -> None:
    lonely = fact_set_of(
        [
            *asserted(12),
            make_fact("F40", stance=ClaimStance.REBUTTED, rebutted_by=("F99",)),
            make_fact("F41", stance=ClaimStance.REBUTTED),
        ]
    )

    assert ids(select_thread_facts(lonely, CAP, ALLOWANCE)) == ids(
        select_thread_facts(fact_set_of(asserted(12)), CAP, ALLOWANCE)
    )


def test_an_allowance_of_zero_offers_asserted_facts_only() -> None:
    selected = select_thread_facts(mixed_set(), CAP, 0)

    assert ids(selected) == [f"F{index}" for index in range(1, CAP + 1)]
    assert selected.disputes == []


def test_a_cap_of_zero_returns_the_whole_fact_set() -> None:
    original = mixed_set()

    assert select_thread_facts(original, 0, ALLOWANCE) is original
    assert select_thread_facts(original, 0, 0) is original


def test_a_set_with_nothing_to_state_falls_back_to_the_whole_set() -> None:
    only_legends = fact_set_of([claimed("F20"), claimed("F21")])

    assert select_thread_facts(only_legends, CAP, 0) is only_legends
    assert ids(select_thread_facts(only_legends, CAP, 1)) == ["F20"]


def test_the_selection_is_deterministic() -> None:
    original = mixed_set()

    first = select_thread_facts(original, CAP, ALLOWANCE)

    assert select_thread_facts(original, CAP, ALLOWANCE) == first
    assert select_thread_facts(original.model_copy(deep=True), CAP, ALLOWANCE) == first


def thread_limits(
    *, max_facts: int = CAP, max_attributed: int = ALLOWANCE, **overrides: int
) -> WritingLimits:
    return WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        thread_max_facts=max_facts,
        thread_max_attributed=max_attributed,
        **overrides,
    )


def tweets(count: int) -> list[str]:
    return [f"Твит номер {index}." for index in range(1, count + 1)]


def user_prompt(fake: ScriptedLLMClient, call: int = 0) -> str:
    messages: list[Message] = fake.calls[call][0]
    return next(message.content for message in messages if message.role is Role.USER)


def offered_ids(prompt: str) -> set[str]:
    return set(FACT_ID.findall(prompt))


async def write_thread(fake: ScriptedLLMClient, limits: WritingLimits, fact_set: FactSet) -> Draft:
    return await write_draft(as_client(fake), fact_set, PostFormat.THREAD, limits, ())


async def test_the_thread_prompt_carries_the_selection_with_its_attributed_blocks() -> None:
    fake = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": tweets(5)})

    await write_thread(fake, thread_limits(), mixed_set())

    prompt = user_prompt(fake)
    assert offered_ids(prompt) == {f"F{index}" for index in range(1, CAP + 1)} | {
        "F20",
        "F30",
        "F31",
    }
    assert "F20 [claimed]:" in prompt
    assert "Disagreement: Даты прибытия расходятся." in prompt
    facts_block = prompt.split("Attributed claims")[0]
    assert "F20" not in facts_block
    assert "F30" not in facts_block
    assert "F11" not in prompt
    assert "F21" not in prompt


async def test_a_rebutted_fact_is_rendered_with_its_offered_rebuttal() -> None:
    fake = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": tweets(5)})

    await write_thread(fake, thread_limits(), rebutted_set(False))

    prompt = user_prompt(fake)
    assert "F40 [rebutted; rebuttal: F12]:" in prompt
    assert "F12 [single]:" in prompt


async def test_used_fact_ids_come_from_the_offered_set_only() -> None:
    fake = ScriptedLLMClient({"fact_ids": ["F1", "F11", "F20", "F21", "F99"], "tweets": tweets(5)})

    draft = await write_thread(fake, thread_limits(), mixed_set())

    assert draft.used_fact_ids == ["F1", "F20"]


async def test_a_disputed_version_in_the_text_is_found_only_when_it_was_offered() -> None:
    texts = ["Первое.", "Мамай подошёл 8 сентября.", "Мамай подошёл 7 сентября.", "Конец."]
    offered = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": texts})
    drift = fact_set_of(
        [
            *asserted(12),
            disputed("F30", "Мамай подошёл 8 сентября."),
            disputed("F31", "Мамай подошёл 7 сентября."),
        ],
        [ARRIVAL_DISPUTE],
    )

    found = await write_thread(offered, thread_limits(max_attributed=1), drift)
    left_out = await write_thread(
        ScriptedLLMClient({"fact_ids": ["F1"], "tweets": texts}),
        thread_limits(max_attributed=0),
        drift,
    )

    assert found.used_fact_ids == ["F1", "F30", "F31"]
    assert left_out.used_fact_ids == ["F1"]


async def test_a_number_of_a_fact_that_was_not_offered_is_unverified() -> None:
    facts = [make_fact(f"F{index}", f"В городе {index} домов.") for index in range(1, 13)]
    fake = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": ["В городе 11 домов.", "Конец."]})

    draft = await write_thread(fake, thread_limits(), fact_set_of(facts))
    unlimited = await write_thread(
        ScriptedLLMClient({"fact_ids": ["F1"], "tweets": ["В городе 11 домов.", "Конец."]}),
        thread_limits(max_facts=0),
        fact_set_of(facts),
    )

    assert draft.unverified_numbers == ["11"]
    assert unlimited.unverified_numbers == []


async def test_a_zero_cap_offers_the_whole_fact_set_to_the_thread() -> None:
    fake = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": tweets(5)})

    await write_thread(fake, thread_limits(max_facts=0), mixed_set())

    assert offered_ids(user_prompt(fake)) == set(ids(mixed_set()))


def test_the_effective_minimum_is_capped_by_the_offered_assertable_facts() -> None:
    limits = thread_limits(max_facts=6, thread_min_tweets=4, thread_min_used_facts=5)
    whole = fact_set_of(asserted(24))

    offered = facts_for_prompt(whole, PostFormat.THREAD, limits)

    assert thread_size(PostFormat.THREAD, limits, offered).model_dump() == {
        "min_tweets": 4,
        "min_facts": 5,
    }
    tiny = thread_limits(max_facts=3, thread_min_tweets=4)
    assert thread_size(
        PostFormat.THREAD, tiny, facts_for_prompt(whole, PostFormat.THREAD, tiny)
    ).model_dump() == {"min_tweets": 3, "min_facts": 0}
    assert thread_size(PostFormat.THREAD, tiny, whole).model_dump() == {
        "min_tweets": 4,
        "min_facts": 0,
    }


def test_the_minimum_ignores_attributed_units_in_the_offered_set() -> None:
    limits = thread_limits(max_facts=CAP, thread_min_tweets=0, thread_min_used_facts=8)
    offered = facts_for_prompt(mixed_set(), PostFormat.THREAD, limits)

    assert len(offered.facts) == CAP + 3
    assert thread_size(PostFormat.THREAD, limits, offered).min_facts == 8
    small = fact_set_of([*asserted(3), claimed("F20"), claimed("F21")])
    capped = facts_for_prompt(small, PostFormat.THREAD, limits)
    assert thread_size(PostFormat.THREAD, limits, capped).min_facts == 3


async def test_a_style_regeneration_is_written_from_the_same_selected_set() -> None:
    fact_set = mixed_set()
    limits = thread_limits()
    writer = ScriptedLLMClient(
        {"fact_ids": ["F1"], "tweets": tweets(5)},
        {"fact_ids": ["F1"], "tweets": tweets(5)},
        {"fact_ids": ["F1"], "tweets": tweets(5)},
    )
    critic = ScriptedLLMClient(
        findings(finding("Твит номер 2.", "filler")),
        findings(finding("Твит номер 3.", "filler")),
        NO_FINDINGS,
    )
    first = await write_draft(as_client(writer), fact_set, PostFormat.THREAD, limits, ())

    result = await review_style(
        as_client(writer),
        as_client(critic),
        first,
        fact_set,
        limits,
        make_style_limits(),
    )

    assert result.regenerations == 2
    assert len(writer.calls) == 3
    sets = [offered_ids(user_prompt(writer, call)) for call in range(3)]
    assert sets[0] == sets[1] == sets[2]
    assert "F11" not in sets[0]
    assert {"F20", "F30", "F31"} <= sets[0]


async def test_the_style_loop_guards_the_minimum_of_the_offered_set() -> None:
    fact_set = fact_set_of(asserted(24))
    limits = thread_limits(max_facts=6, thread_min_tweets=4, thread_min_used_facts=5)
    small = {"fact_ids": ["F1", "F2"], "tweets": tweets(3)}
    writer = ScriptedLLMClient(small, small)
    critic = ScriptedLLMClient(findings(finding("Твит номер 2.", "filler")))
    first = Draft(
        post_format=PostFormat.THREAD,
        parts=[DraftPart(text=text) for text in tweets(4)],
        used_fact_ids=["F1", "F2", "F3", "F4", "F5"],
        unverified_numbers=[],
        length_violations=[],
        attempts=1,
    )

    result = await review_style(
        as_client(writer),
        as_client(critic),
        first,
        fact_set,
        limits,
        make_style_limits(max_regenerations=1),
    )

    assert result.regressions_rejected == 1
    assert result.draft.texts == tweets(4)


async def test_the_thread_selection_is_logged_with_numbers_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    fake = ScriptedLLMClient({"fact_ids": ["F1"], "tweets": tweets(5)})

    await write_thread(fake, thread_limits(), mixed_set())

    lines = [record.getMessage() for record in caplog.records]
    assert "thread facts total=17 assertable=12 offered=13 offered_assertable=10" in lines
    assert not any("Факт номер" in line for line in lines)


def test_short_and_long_are_unchanged_by_the_thread_settings() -> None:
    limits = thread_limits()
    original = mixed_set()

    assert facts_for_prompt(original, PostFormat.LONG, limits) is original
    assert facts_for_prompt(original, PostFormat.SHORT, limits) == select_short_facts(
        original, limits.short_max_facts
    )


async def test_a_short_post_still_checks_numbers_against_the_whole_fact_set() -> None:
    facts = [make_fact(f"F{index}", f"В городе {index} домов.") for index in range(1, 13)]
    fake = ScriptedLLMClient({"fact_ids": ["F1", "F11"], "text": "В городе 11 домов."})

    draft = await write_draft(
        as_client(fake), fact_set_of(facts), PostFormat.SHORT, thread_limits(), ()
    )

    assert draft.unverified_numbers == []
    assert draft.used_fact_ids == ["F1", "F11"]


def test_the_writing_limits_reject_a_cap_below_the_gate_or_the_used_minimum() -> None:
    with pytest.raises(ValidationError, match="thread fact cap"):
        thread_limits(max_facts=4, thread_min_facts=5)
    with pytest.raises(ValidationError, match="thread fact cap"):
        thread_limits(max_facts=4, thread_min_used_facts=5)

    assert thread_limits(max_facts=5, thread_min_facts=5, thread_min_used_facts=5)
    assert thread_limits(max_facts=0, thread_min_facts=5, thread_min_used_facts=5)
    assert thread_limits(max_facts=3, thread_min_facts=0, thread_min_used_facts=0)


def test_the_documented_defaults() -> None:
    assert THREAD_DEFAULT_MAX_FACTS == CAP
    assert THREAD_DEFAULT_MAX_ATTRIBUTED == ALLOWANCE
