import logging

import pytest
from app.domain.draft import PostFormat
from app.domain.llm import Message
from app.domain.style import StyleResult, StyleRule
from app.services.generator import WritingLimits
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


THREAD_FILLER_TWEETS = [
    "Первый твит. Это решило многое.",
    "Второй твит.",
    "Третий твит.",
    "Четвёртый твит.",
]
THREAD_FACTS = ("F1", "F2", "F3", "F4")
FOUR_TWEETS = ["Один.", "Два.", "Три.", "Четыре."]
THREE_TWEETS = ["Один.", "Два.", "Три."]
TWO_TWEETS = ["Один.", "Два."]


def thread_limits() -> WritingLimits:
    return WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        thread_min_tweets=4,
        thread_min_used_facts=5,
    )


def thread_with(tweets: list[str], *facts: str) -> dict[str, object]:
    return {"fact_ids": list(facts), "tweets": tweets}


async def review_thread(
    writer: ScriptedLLMClient,
    critic: ScriptedLLMClient,
    *,
    tweets: list[str],
    used: tuple[str, ...] = THREAD_FACTS,
    max_regenerations: int = 1,
) -> StyleResult:
    return await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(tweets, PostFormat.THREAD, used_fact_ids=used),
        FACT_SET,
        thread_limits(),
        make_style_limits(max_regenerations=max_regenerations),
    )


async def test_a_thread_regenerated_below_the_tweet_minimum_is_rejected_and_never_chosen() -> None:
    writer = ScriptedLLMClient(
        thread_with(THREE_TWEETS, *THREAD_FACTS), thread_with(THREE_TWEETS, *THREAD_FACTS)
    )
    critic = ScriptedLLMClient(FILLER_FINDING)

    result = await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS)

    assert result.regressions_rejected == 1
    assert (result.attempts, result.chosen_attempt) == (1, 1)
    assert result.draft.texts == THREAD_FILLER_TWEETS
    assert len(critic.calls) == 1


async def test_a_thread_regenerated_below_the_fact_minimum_is_rejected() -> None:
    writer = ScriptedLLMClient(
        thread_with(FOUR_TWEETS, "F1", "F2", "F3"), thread_with(FOUR_TWEETS, "F1", "F2", "F3")
    )
    critic = ScriptedLLMClient(FILLER_FINDING)

    result = await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS)

    assert result.regressions_rejected == 1
    assert result.draft.texts == THREAD_FILLER_TWEETS


async def test_the_thread_guard_works_where_the_ratio_thresholds_pass() -> None:
    critic = ScriptedLLMClient(FILLER_FINDING)
    long_tweets = [f"{index}" + "а" * 200 for index in range(4)]
    long_tweets[0] += f" {FILLER}"
    shrunk = [f"{index}" + "а" * 230 for index in range(3)]
    writer = ScriptedLLMClient(
        thread_with(shrunk, *THREAD_FACTS), thread_with(shrunk, *THREAD_FACTS)
    )

    result = await review_thread(writer, critic, tweets=long_tweets)

    assert result.regressions_rejected == 1


async def test_a_thread_that_keeps_the_minimum_is_accepted() -> None:
    writer = ScriptedLLMClient(thread_with(FOUR_TWEETS, *THREAD_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS)

    assert result.regressions_rejected == 0
    assert result.draft.texts == FOUR_TWEETS
    assert REGRESSION_NOTE not in revision_prompt(writer, 0)


async def test_a_first_variant_already_below_the_minimum_is_not_punished_twice() -> None:
    first = ["Первый твит. Это решило многое.", "Второй твит.", "Третий твит."]
    writer = ScriptedLLMClient(
        thread_with(THREE_TWEETS, *THREAD_FACTS), thread_with(THREE_TWEETS, *THREAD_FACTS)
    )
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_thread(writer, critic, tweets=first)

    assert result.regressions_rejected == 0
    assert result.draft.texts == THREE_TWEETS


async def test_a_variant_smaller_than_a_first_variant_that_was_already_small_is_rejected() -> None:
    first = ["Первый твит. Это решило многое.", "Второй твит.", "Третий твит."]
    writer = ScriptedLLMClient(
        thread_with(TWO_TWEETS, *THREAD_FACTS), thread_with(TWO_TWEETS, *THREAD_FACTS)
    )
    critic = ScriptedLLMClient(FILLER_FINDING)

    result = await review_thread(writer, critic, tweets=first)

    assert result.regressions_rejected == 1
    assert result.draft.texts == first


async def test_a_rejected_thread_regression_adds_the_stronger_instruction_to_the_next_try() -> None:
    writer = ScriptedLLMClient(
        thread_with(THREE_TWEETS, *THREAD_FACTS),
        thread_with(THREE_TWEETS, *THREAD_FACTS),
        thread_with(FOUR_TWEETS, *THREAD_FACTS),
    )
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS, max_regenerations=2)

    assert result.regressions_rejected == 1
    assert result.draft.texts == FOUR_TWEETS
    assert REGRESSION_NOTE in revision_prompt(writer, 2)


async def test_long_and_short_ignore_the_thread_minimum_in_the_guard() -> None:
    writer = ScriptedLLMClient(single_reply("б" * 900, *FIVE_FACTS))
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([WITH_FILLER], PostFormat.LONG, used_fact_ids=FIVE_FACTS),
        FACT_SET,
        thread_limits(),
        make_style_limits(max_regenerations=1),
    )

    assert result.regressions_rejected == 0
    assert result.draft.texts == ["б" * 900]


def style_line(caplog: pytest.LogCaptureFixture) -> str:
    lines = [record.getMessage() for record in caplog.records if "style reviewed" in record.message]
    assert len(lines) == 1
    return lines[0]


async def test_the_style_line_logs_the_triggers_and_the_regression_causes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = ScriptedLLMClient(
        thread_with(THREE_TWEETS, *THREAD_FACTS),
        thread_with(THREE_TWEETS, *THREAD_FACTS),
        thread_with(FOUR_TWEETS, *THREAD_FACTS),
    )
    critic = ScriptedLLMClient(FILLER_FINDING, NO_FINDINGS)

    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS, max_regenerations=2)

    line = style_line(caplog)
    assert "regeneration_triggers=filler=1|filler=1" in line
    assert f"regression_causes=thread_parts={len(THREAD_FILLER_TWEETS)}>3" in line
    assert FILLER not in line


async def test_the_style_line_logs_none_when_nothing_was_regenerated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(NO_FINDINGS)

    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS)

    line = style_line(caplog)
    assert "regeneration_triggers=none" in line
    assert "regression_causes=none" in line


async def test_a_fact_loss_is_logged_with_both_counts(caplog: pytest.LogCaptureFixture) -> None:
    writer = ScriptedLLMClient(
        thread_with(FOUR_TWEETS, "F1", "F2", "F3"), thread_with(FOUR_TWEETS, "F1", "F2", "F3")
    )
    critic = ScriptedLLMClient(FILLER_FINDING)

    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await review_thread(writer, critic, tweets=THREAD_FILLER_TWEETS)

    assert f"thread_facts={len(THREAD_FACTS)}>3" in style_line(caplog)
