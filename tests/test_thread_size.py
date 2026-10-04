import logging

import pytest
from app.config.constants import THREAD_TYPICAL_SIZE_MULTIPLIER
from app.config.settings import Settings
from app.domain.draft import Draft, LengthIssue, LengthViolation, PostFormat, ThreadSize
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Message, Role
from app.services.generator import WritingLimits, thread_size, write_draft
from pydantic import ValidationError

from llm_helpers import ScriptedLLMClient, as_client

MIN_TWEETS = 4
MIN_FACTS = 5
SOURCE_URL = "https://unique-thread-source.example.org/day"
TWEETS_OF_FOUR = ["Первый твит.", "Второй твит.", "Третий твит.", "Четвёртый твит."]
TWEETS_OF_TWO = ["Первый твит.", "Второй твит."]
ASSERTABLE_IDS = ("F1", "F2", "F3", "F4", "F5", "F6", "F7")


def make_fact(fact_id: str, text: str, status: FactStatus = FactStatus.SINGLE) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[SourceRef(snippet_id="abc", url=SOURCE_URL, domain="example.org", quote=text)],
        status=status,
    )


def make_fact_set(assertable: int) -> FactSet:
    facts = [make_fact(f"F{index}", f"Факт номер {index}.") for index in range(1, assertable + 1)]
    return FactSet(
        topic="Обычный день",
        facts=[
            *facts,
            make_fact("F50", "Тираж оценивают в 100 тысяч.", FactStatus.DISPUTED),
            make_fact("F51", "Тираж составлял 200 тысяч.", FactStatus.DISPUTED),
        ],
        disputes=[Dispute(fact_ids=["F50", "F51"], explanation="Тиражи расходятся.")],
    )


FACT_SET = make_fact_set(7)


def make_limits(
    *,
    thread_min_tweets: int = MIN_TWEETS,
    thread_min_used_facts: int = MIN_FACTS,
    thread_max_tweets: int = 12,
    thread_numbering: bool = False,
) -> WritingLimits:
    return WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=thread_max_tweets,
        thread_min_tweets=thread_min_tweets,
        thread_min_used_facts=thread_min_used_facts,
        thread_numbering=thread_numbering,
    )


def thread(tweets: list[str], *fact_ids: str) -> dict[str, object]:
    return {"fact_ids": list(fact_ids), "tweets": tweets}


async def write_thread(
    fake: ScriptedLLMClient,
    limits: WritingLimits | None = None,
    fact_set: FactSet = FACT_SET,
) -> Draft:
    return await write_draft(
        as_client(fake), fact_set, PostFormat.THREAD, limits or make_limits(), ()
    )


def system_text(messages: list[Message]) -> str:
    return "\n".join(message.content for message in messages if message.role is Role.SYSTEM)


def correction(fake: ScriptedLLMClient, call: int) -> str:
    return fake.calls[call][0][-1].content


async def test_a_two_tweet_thread_is_retried_with_its_size_and_the_unused_facts() -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_TWO, "F1", "F2", "F3", "F4"), thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5])
    )

    draft = await write_thread(fake)

    assert len(fake.calls) == 2
    assert draft.attempts == 2
    assert draft.texts == TWEETS_OF_FOUR
    assert draft.length_violations == []
    message = correction(fake, 1)
    assert "It has 2 tweets and uses 4 of the facts you may state" in message
    assert "the minimum is 4 tweets and 5 facts" in message
    assert "- 2 tweets, the minimum is 4" in message
    assert "- 4 facts used, the minimum is 5" in message
    assert "Facts not used yet: F5, F6, F7." in message
    assert "Do not pad the thread" in message
    assert "not make it one fact per tweet" in message
    assert "at most 280 characters" in message
    assert "F50" not in message
    assert "F51" not in message
    assert "Факт номер" not in message


async def test_a_thread_that_stays_small_is_returned_with_both_violations() -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_TWO, "F1", "F2"), thread(TWEETS_OF_TWO, "F1", "F2", "F3")
    )

    draft = await write_thread(fake)

    assert len(fake.calls) == 2
    assert draft.attempts == 2
    assert draft.texts == TWEETS_OF_TWO
    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_PARTS, actual=2, limit=MIN_TWEETS),
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=3, limit=MIN_FACTS),
    ]


async def test_too_few_tweets_is_a_violation_alone_when_the_facts_are_enough() -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_TWO[:1] * 3, *ASSERTABLE_IDS[:5]),
        thread(TWEETS_OF_TWO[:1] * 3, *ASSERTABLE_IDS[:5]),
    )

    draft = await write_thread(fake)

    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_PARTS, actual=3, limit=MIN_TWEETS)
    ]
    assert "- 3 tweets, the minimum is 4" in correction(fake, 1)
    assert "facts used, the minimum" not in correction(fake, 1)
    assert "It has 3 tweets and uses 5 of the facts" in correction(fake, 1)


async def test_too_few_facts_is_a_violation_alone_when_the_tweets_are_enough() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, "F1", "F2"), thread(TWEETS_OF_FOUR, "F1", "F2"))

    draft = await write_thread(fake)

    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=2, limit=MIN_FACTS)
    ]
    assert "- 2 facts used, the minimum is 5" in correction(fake, 1)
    assert "tweets, the minimum is 4\n" not in correction(fake, 1)
    assert "It has 4 tweets and uses 2 of the facts" in correction(fake, 1)


async def test_the_exact_minimum_passes_without_a_retry() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    draft = await write_thread(fake)

    assert len(fake.calls) == 1
    assert draft.length_violations == []


async def test_unknown_fact_ids_do_not_count_as_used_facts() -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_FOUR, "F1", "F2", "F3", "F4", "F99"),
        thread(TWEETS_OF_FOUR, "F1", "F2", "F3", "F4", "F99"),
    )

    draft = await write_thread(fake)

    assert [violation.issue for violation in draft.length_violations] == [LengthIssue.TOO_FEW_FACTS]


async def test_a_disputed_fact_stated_by_its_numbers_counts_as_used() -> None:
    tweets = [*TWEETS_OF_FOUR[:3], "Тираж оценивают в 100 тысяч и в 200 тысяч."]
    fake = ScriptedLLMClient(thread(tweets, "F1", "F2", "F3"))

    draft = await write_thread(fake)

    assert draft.used_fact_ids == ["F1", "F2", "F3", "F50", "F51"]
    assert draft.length_violations == []


async def test_an_overlong_tweet_and_too_few_tweets_are_corrected_together() -> None:
    long_tweet = "а" * 300
    fake = ScriptedLLMClient(
        thread([long_tweet, "Второй твит."], *ASSERTABLE_IDS[:5]),
        thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]),
    )

    draft = await write_thread(fake)

    message = correction(fake, 1)
    assert "- part 1: 300 characters, the limit is 280" in message
    assert "- 2 tweets, the minimum is 4" in message
    assert draft.length_violations == []


async def test_the_correction_limit_leaves_room_for_the_numbering() -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_TWO, *ASSERTABLE_IDS[:5]), thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5])
    )

    await write_thread(fake, make_limits(thread_numbering=True))

    assert "at most 276 characters" in correction(fake, 1)


def test_the_effective_minimum_is_capped_by_the_facts_that_can_be_stated() -> None:
    size = thread_size(PostFormat.THREAD, make_limits(), make_fact_set(3))

    assert size == ThreadSize(min_tweets=3, min_facts=3)


def test_the_effective_tweet_minimum_never_exceeds_the_effective_fact_minimum() -> None:
    limits = make_limits(thread_min_tweets=6, thread_min_used_facts=5)

    assert thread_size(PostFormat.THREAD, limits, make_fact_set(5)) == ThreadSize(
        min_tweets=5, min_facts=5
    )
    assert thread_size(PostFormat.THREAD, limits, make_fact_set(8)) == ThreadSize(
        min_tweets=5, min_facts=5
    )


def test_with_the_fact_minimum_off_the_tweet_minimum_is_capped_by_the_facts_on_offer() -> None:
    limits = make_limits(thread_min_tweets=4, thread_min_used_facts=0)

    assert thread_size(PostFormat.THREAD, limits, make_fact_set(3)) == ThreadSize(
        min_tweets=3, min_facts=0
    )
    assert thread_size(PostFormat.THREAD, limits, make_fact_set(9)) == ThreadSize(
        min_tweets=4, min_facts=0
    )


def test_the_size_is_off_when_the_limits_do_not_set_it_and_for_other_formats() -> None:
    off = make_limits(thread_min_tweets=0, thread_min_used_facts=0)

    assert thread_size(PostFormat.THREAD, off, FACT_SET) == ThreadSize(min_tweets=0, min_facts=0)
    assert thread_size(PostFormat.LONG, make_limits(), FACT_SET) == ThreadSize(
        min_tweets=0, min_facts=0
    )
    assert thread_size(PostFormat.SHORT, make_limits(), FACT_SET) == ThreadSize(
        min_tweets=0, min_facts=0
    )


async def test_a_small_set_is_not_asked_for_more_than_it_has() -> None:
    small = make_fact_set(3)
    enough = ScriptedLLMClient(thread(TWEETS_OF_FOUR[:3], "F1", "F2", "F3"))
    short_by_one = ScriptedLLMClient(
        thread(TWEETS_OF_FOUR[:3], "F1", "F2"), thread(TWEETS_OF_FOUR[:3], "F1", "F2")
    )

    passed = await write_thread(enough, fact_set=small)
    failed = await write_thread(short_by_one, fact_set=small)

    assert passed.length_violations == []
    assert failed.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=2, limit=3)
    ]


async def test_the_thread_minimum_is_off_when_the_limits_do_not_set_it() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_TWO, "F1"))

    draft = await write_thread(fake, make_limits(thread_min_tweets=0, thread_min_used_facts=0))

    assert len(fake.calls) == 1
    assert draft.length_violations == []
    prompt = system_text(fake.calls[0][0])
    assert "a thread of 2 to 12 tweets" in prompt
    assert "ceiling" not in prompt
    assert "Develop at least" not in prompt


async def test_short_and_long_ignore_the_thread_minimum() -> None:
    short_fake = ScriptedLLMClient({"fact_ids": ["F1"], "text": "Коротко."})
    long_fake = ScriptedLLMClient({"fact_ids": ["F1"], "text": "Длинно."})
    limits = make_limits()

    short = await write_draft(as_client(short_fake), FACT_SET, PostFormat.SHORT, limits, ())
    long = await write_draft(as_client(long_fake), FACT_SET, PostFormat.LONG, limits, ())

    assert len(short_fake.calls) == 1
    assert len(long_fake.calls) == 1
    assert short.length_violations == []
    assert long.length_violations == []
    assert "tweets" not in system_text(short_fake.calls[0][0]).split("Rules:")[0]
    assert "a thread of" not in system_text(short_fake.calls[0][0])
    assert "a thread of" not in system_text(long_fake.calls[0][0])
    assert "Make it as long as the facts deserve" in system_text(long_fake.calls[0][0])


async def test_the_thread_prompt_states_the_size_and_keeps_the_grouping_rule() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    await write_thread(fake)

    prompt = system_text(fake.calls[0][0])
    assert "a thread of 4 to 12 tweets" in prompt
    assert "Develop at least 5 of the facts you may state across the thread" in prompt
    assert (
        "Keep related facts together in one tweet by meaning: it is not one fact per tweet"
        in prompt
    )
    assert "The maximum of 12 tweets is a ceiling, not a target" in prompt
    assert "do not shrink it to a summary of two or three tweets" in prompt
    assert "A tweet may be one short sentence and needs no closing line of its own" in prompt


async def test_the_prompt_numbers_follow_the_limits_and_the_facts_on_offer() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR[:3], "F1", "F2", "F3"))

    await write_thread(
        fake, make_limits(thread_min_tweets=6, thread_min_used_facts=5), make_fact_set(3)
    )

    prompt = system_text(fake.calls[0][0])
    assert "a thread of 3 to 12 tweets" in prompt
    assert "Develop at least 3 of the facts" in prompt


def test_the_limits_reject_a_tweet_minimum_above_the_maximum() -> None:
    with pytest.raises(ValidationError, match="thread minimum"):
        WritingLimits(
            short_max_chars=280,
            long_max_chars=25000,
            thread_tweet_max_chars=280,
            thread_max_tweets=5,
            thread_min_tweets=6,
        )


def test_the_limits_take_the_thread_minimums_from_the_settings() -> None:
    settings = Settings(
        telegram_bot_token="token",
        owner_telegram_ids=[1],
        thread_min_tweets=3,
        thread_min_used_facts=4,
    )

    limits = WritingLimits.from_settings(settings)

    assert (limits.thread_min_tweets, limits.thread_min_used_facts) == (3, 4)


async def test_the_prompt_gives_a_typical_range_derived_from_the_minimum() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    await write_thread(fake)

    typical_max = MIN_TWEETS * THREAD_TYPICAL_SIZE_MULTIPLIER
    prompt = system_text(fake.calls[0][0])
    assert f"A thread usually has from {MIN_TWEETS} to {typical_max} tweets." in prompt
    assert "a thread of 4 to 12 tweets" in prompt
    assert "The maximum of 12 tweets is a ceiling, not a target" in prompt


async def test_the_typical_range_follows_the_settings() -> None:
    settings = Settings(
        telegram_bot_token="token",
        owner_telegram_ids=[1],
        thread_min_tweets=3,
        thread_min_used_facts=3,
    )
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR[:3], "F1", "F2", "F3"))

    await write_thread(fake, WritingLimits.from_settings(settings))

    expected = f"from 3 to {3 * THREAD_TYPICAL_SIZE_MULTIPLIER} tweets"
    assert expected in system_text(fake.calls[0][0])


async def test_the_typical_range_never_passes_the_ceiling() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    await write_thread(fake, make_limits(thread_max_tweets=6))

    prompt = system_text(fake.calls[0][0])
    assert "A thread usually has from 4 to 6 tweets." in prompt
    assert "The maximum of 6 tweets is a ceiling, not a target" in prompt


async def test_there_is_no_typical_range_when_it_would_equal_the_minimum() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    await write_thread(fake, make_limits(thread_max_tweets=MIN_TWEETS))

    assert "usually" not in system_text(fake.calls[0][0])


async def test_there_is_no_typical_range_without_a_tweet_minimum() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    await write_thread(fake, make_limits(thread_min_tweets=0))

    prompt = system_text(fake.calls[0][0])
    assert "usually" not in prompt
    assert "Develop at least 5 of the facts" in prompt


async def test_there_is_no_typical_range_when_the_minimums_are_off() -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_TWO))

    await write_thread(fake, make_limits(thread_min_tweets=0, thread_min_used_facts=0))

    prompt = system_text(fake.calls[0][0])
    assert "usually" not in prompt
    assert "ceiling, not a target" not in prompt


async def test_the_length_retry_logs_the_kinds_and_numbers_without_texts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = ScriptedLLMClient(
        thread(TWEETS_OF_TWO, "F1", "F2", "F3", "F4"), thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5])
    )

    with caplog.at_level(logging.INFO, logger="app.services.generator"):
        await write_thread(fake)

    retries = [record.getMessage() for record in caplog.records if "length retry" in record.message]
    assert len(retries) == 1
    assert "attempt=1" in retries[0]
    assert "parts=2" in retries[0]
    assert "used_facts=4" in retries[0]
    assert "too_few_parts=2/4,too_few_facts=4/5" in retries[0]
    assert "твит" not in retries[0]


async def test_the_length_retry_log_names_the_overlong_tweet_by_its_number(
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_tweet = "а" * 300
    fake = ScriptedLLMClient(
        thread([TWEETS_OF_FOUR[0], long_tweet, *TWEETS_OF_FOUR[2:]], *ASSERTABLE_IDS[:5]),
        thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]),
    )

    with caplog.at_level(logging.INFO, logger="app.services.generator"):
        await write_thread(fake)

    retries = [record.getMessage() for record in caplog.records if "length retry" in record.message]
    assert "part_too_long#2=300/280" in retries[0]
    assert "а" * 10 not in retries[0]


async def test_no_retry_is_logged_when_the_first_draft_fits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = ScriptedLLMClient(thread(TWEETS_OF_FOUR, *ASSERTABLE_IDS[:5]))

    with caplog.at_level(logging.INFO, logger="app.services.generator"):
        await write_thread(fake)

    assert "length retry" not in caplog.text
