import pytest
from app.domain.draft import PostFormat
from app.domain.llm import Message
from app.domain.style import StyleResult, StyleRule
from app.services.style_review import StyleLimits, review_style

from llm_helpers import ScriptedLLMClient, as_client
from style_helpers import (
    FACT_SET,
    NO_FINDINGS,
    finding,
    findings,
    make_draft,
    make_style_limits,
    make_writing_limits,
    single_reply,
    thread_reply,
)

FILLER = "Это решило многое."
PREVIOUS_CHARS = 1000
BODY = "а" * (PREVIOUS_CHARS - len(FILLER) - 1)
WITH_FILLER = f"{BODY} {FILLER}"
FIVE_FACTS = ("F1", "F2", "F3", "F4", "F5")
REGRESSION_NOTE = "The previous attempt removed too much"
STUB = "Коротко."
FILLER_FINDING = findings(finding(FILLER, "filler"))


def revision_prompt(writer: ScriptedLLMClient, index: int) -> str:
    messages: list[Message] = writer.calls[index][0]
    return messages[-1].content


async def review_long(
    writer: ScriptedLLMClient,
    critic: ScriptedLLMClient,
    *,
    text: str = WITH_FILLER,
    used: tuple[str, ...] = FIVE_FACTS,
    limits: StyleLimits | None = None,
) -> StyleResult:
    return await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([text], PostFormat.LONG, used_fact_ids=used),
        FACT_SET,
        make_writing_limits(),
        limits or make_style_limits(),
    )


async def test_a_regression_is_rejected_never_chosen_and_the_loop_tries_again() -> None:
    fixed = "б" * 900
    writer = ScriptedLLMClient(single_reply(STUB, "F1"), single_reply(fixed, *FIVE_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(writer, critic)

    assert len(writer.calls) == 2
    assert len(critic.calls) == 2
    assert result.regressions_rejected == 1
    assert result.regenerations == 2
    assert (result.attempts, result.chosen_attempt) == (2, 2)
    assert result.draft.texts == [fixed]
    assert result.report.passed
    assert REGRESSION_NOTE not in revision_prompt(writer, 0)
    assert REGRESSION_NOTE in revision_prompt(writer, 1)
    assert "change only the flagged fragments" in revision_prompt(writer, 1)


async def test_a_regression_does_not_become_the_previous_text_of_the_next_attempt() -> None:
    writer = ScriptedLLMClient(single_reply(STUB, "F1"), single_reply("б" * 900, *FIVE_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    await review_long(writer, critic)

    second = revision_prompt(writer, 1)
    assert f"Previous version of the post:\n{WITH_FILLER}" in second
    assert STUB not in second


async def test_when_every_attempt_regresses_the_original_goes_out_with_its_violations() -> None:
    writer = ScriptedLLMClient(single_reply(STUB, "F1"), single_reply(STUB, "F1"))
    critic = ScriptedLLMClient(FILLER_FINDING)

    result = await review_long(writer, critic)

    assert result.regressions_rejected == 2
    assert result.regenerations == 2
    assert len(critic.calls) == 1
    assert (result.attempts, result.chosen_attempt) == (1, 1)
    assert result.draft.texts == [WITH_FILLER]
    assert [violation.rule for violation in result.report.violations] == [StyleRule.FILLER]
    assert not result.regeneration_failed


async def test_the_best_earlier_variant_is_kept_when_the_budget_ends_on_a_regression() -> None:
    still_flawed = "в" * 700 + f" {FILLER}"
    writer = ScriptedLLMClient(single_reply(still_flawed, *FIVE_FACTS), single_reply(STUB, "F1"))
    critic = ScriptedLLMClient(FILLER_FINDING, FILLER_FINDING)

    result = await review_long(writer, critic, limits=make_style_limits(max_regenerations=2))

    assert result.regressions_rejected == 1
    assert result.draft.texts == [still_flawed]
    assert (result.attempts, result.chosen_attempt) == (2, 2)
    assert len(result.report.violations) == 1
    assert REGRESSION_NOTE not in revision_prompt(writer, 1)
    assert f"Previous version of the post:\n{still_flawed}" in revision_prompt(writer, 1)


async def test_a_zero_budget_never_calls_the_writer() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(FILLER_FINDING)

    result = await review_long(writer, critic, limits=make_style_limits(max_regenerations=0))

    assert writer.calls == []
    assert result.regressions_rejected == 0


async def test_a_legitimate_shortening_above_the_threshold_is_accepted() -> None:
    fixed = "г" * 750
    writer = ScriptedLLMClient(single_reply(fixed, *FIVE_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(writer, critic)

    assert result.regressions_rejected == 0
    assert result.regenerations == 1
    assert result.draft.texts == [fixed]
    assert REGRESSION_NOTE not in revision_prompt(writer, 0)


@pytest.mark.parametrize(
    ("new_chars", "rejected"),
    [(PREVIOUS_CHARS * 6 // 10, False), (PREVIOUS_CHARS * 6 // 10 - 1, True)],
)
async def test_the_character_threshold_is_checked_on_its_own(
    new_chars: int, rejected: bool
) -> None:
    writer = ScriptedLLMClient(
        single_reply("д" * new_chars, *FIVE_FACTS), single_reply("д" * new_chars, *FIVE_FACTS)
    )
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS, NO_FINDINGS)

    result = await review_long(writer, critic, limits=make_style_limits(max_regenerations=1))

    assert result.regressions_rejected == (1 if rejected else 0)


@pytest.mark.parametrize(("kept_facts", "rejected"), [(3, False), (2, True)])
async def test_the_fact_threshold_is_checked_on_its_own(kept_facts: int, rejected: bool) -> None:
    writer = ScriptedLLMClient(single_reply("е" * 900, *FIVE_FACTS[:kept_facts]))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(writer, critic, limits=make_style_limits(max_regenerations=1))

    assert result.regressions_rejected == (1 if rejected else 0)
    assert (result.draft.texts == [WITH_FILLER]) is rejected


@pytest.mark.parametrize(
    ("previous", "new", "rejected"),
    [(150, 40, True), (120, 30, False), (100, 10, False), (101, 0 + 1, False), (200, 99, True)],
)
async def test_a_small_post_may_lose_one_flagged_sentence_without_a_regression(
    previous: int, new: int, rejected: bool
) -> None:
    text = "ж" * (previous - len(FILLER) - 1) + f" {FILLER}"
    writer = ScriptedLLMClient(single_reply("з" * new, "F1"))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")), NO_FINDINGS)
    draft = make_draft([text], PostFormat.SHORT, used_fact_ids=("F1",))

    result = await review_style(
        as_client(writer),
        as_client(critic),
        draft,
        FACT_SET,
        make_writing_limits(),
        make_style_limits(max_regenerations=1),
    )

    assert result.regressions_rejected == (1 if rejected else 0)


async def test_losing_one_of_two_facts_is_not_a_regression() -> None:
    text = "и" * 400 + f" {FILLER}"
    writer = ScriptedLLMClient(single_reply("и" * 400, "F1"))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(
        writer, critic, text=text, used=("F1", "F2"), limits=make_style_limits(max_regenerations=1)
    )

    assert result.regressions_rejected == 0
    assert result.draft.texts == ["и" * 400]


async def test_no_previous_facts_means_no_fact_regression() -> None:
    writer = ScriptedLLMClient(single_reply("к" * 900, "F1"))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(
        writer, critic, used=(), limits=make_style_limits(max_regenerations=1)
    )

    assert result.regressions_rejected == 0


async def test_a_thread_is_guarded_by_the_total_of_its_parts() -> None:
    tweets = ["л" * 250, "м" * 250, f"{'н' * 200} {FILLER}"]
    writer = ScriptedLLMClient(thread_reply("Один.", "Два."))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")))

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(tweets, PostFormat.THREAD, used_fact_ids=FIVE_FACTS),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(max_regenerations=1),
    )

    assert result.regressions_rejected == 1
    assert result.draft.texts == tweets
    assert len(critic.calls) == 1


async def test_the_ratios_come_from_the_limits() -> None:
    writer = ScriptedLLMClient(single_reply("о" * 700, *FIVE_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_long(
        writer,
        critic,
        limits=make_style_limits(max_regenerations=1, min_retained_chars_ratio=0.8),
    )

    assert result.regressions_rejected == 1
