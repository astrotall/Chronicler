import json

import pytest
from app.config.style import BANNED_PHRASES
from app.domain.draft import (
    Draft,
    DraftPart,
    LengthIssue,
    LengthViolation,
    PostFormat,
    Revision,
)
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Message, Role
from app.llm.errors import LLMInvalidResponseError, LLMUnavailableError
from app.services.generator import (
    SingleReply,
    ThreadReply,
    WritingLimits,
    unverified_numbers,
    write_draft,
)
from pydantic import ValidationError

from llm_helpers import ScriptedLLMClient, as_client

DATE_QUOTE = "UNIQUE-QUOTE-DATE: the battle was fought on 8 September 1380"
WINNER_QUOTE = "UNIQUE-QUOTE-WINNER: войско Дмитрия разбило войско Мамая"
PLACE_QUOTE = "UNIQUE-QUOTE-PLACE: у впадения Непрядвы в Дон"
ARMY_60_QUOTE = "UNIQUE-QUOTE-60: оценивают в 60 000 человек"
ARMY_150_QUOTE = "UNIQUE-QUOTE-150: составляла 150 000 человек"
SOURCE_URL = "https://unique-source.example.org/kulikovo"

DATE_TEXT = "Куликовская битва произошла 8 сентября 1380 года."
WINNER_TEXT = "Войско Дмитрия Донского разбило войско Мамая."
PLACE_TEXT = "Битва шла у впадения Непрядвы в Дон."
ARMY_60_TEXT = "Численность русского войска оценивают в 60 000 человек."
ARMY_150_TEXT = "Численность русского войска составляла 150 000 человек."
DISPUTE_EXPLANATION = "Оценки численности русского войска расходятся."
TOPIC = "Куликовская битва"
DISPUTES_HEADER = "Disputed facts."
FACTS_HEADER = "Facts you may state:"


def make_fact(fact_id: str, text: str, quote: str, status: FactStatus) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[SourceRef(snippet_id="abc", url=SOURCE_URL, domain="example.org", quote=quote)],
        status=status,
    )


FACT_SET = FactSet(
    topic=TOPIC,
    facts=[
        make_fact("F1", DATE_TEXT, DATE_QUOTE, FactStatus.CONFIRMED),
        make_fact("F2", WINNER_TEXT, WINNER_QUOTE, FactStatus.SINGLE),
        make_fact("F3", PLACE_TEXT, PLACE_QUOTE, FactStatus.SINGLE),
        make_fact("F4", ARMY_60_TEXT, ARMY_60_QUOTE, FactStatus.DISPUTED),
        make_fact("F5", ARMY_150_TEXT, ARMY_150_QUOTE, FactStatus.DISPUTED),
    ],
    disputes=[Dispute(fact_ids=["F4", "F5"], explanation=DISPUTE_EXPLANATION)],
)


def make_limits(
    *,
    short_max_chars: int = 280,
    long_max_chars: int = 25000,
    thread_tweet_max_chars: int = 280,
    thread_max_tweets: int = 12,
    thread_numbering: bool = False,
) -> WritingLimits:
    return WritingLimits(
        short_max_chars=short_max_chars,
        long_max_chars=long_max_chars,
        thread_tweet_max_chars=thread_tweet_max_chars,
        thread_max_tweets=thread_max_tweets,
        thread_numbering=thread_numbering,
    )


LIMITS = make_limits()


def single(text: str, *fact_ids: str) -> dict[str, object]:
    return {"fact_ids": list(fact_ids), "text": text}


def thread(*tweets: str, fact_ids: tuple[str, ...] = ("F1",)) -> dict[str, object]:
    return {"fact_ids": list(fact_ids), "tweets": list(tweets)}


def all_text(messages: list[Message]) -> str:
    return "\n".join(message.content for message in messages)


def user_text(messages: list[Message]) -> str:
    [user] = [message for message in messages if message.role is Role.USER]
    return user.content


def system_text(messages: list[Message]) -> str:
    return "\n".join(message.content for message in messages if message.role is Role.SYSTEM)


async def write(
    fake: ScriptedLLMClient,
    post_format: PostFormat = PostFormat.SHORT,
    limits: WritingLimits = LIMITS,
    examples: tuple[str, ...] = (),
    *,
    fact_set: FactSet = FACT_SET,
    angle: str | None = None,
    revision: Revision | None = None,
) -> Draft:
    return await write_draft(
        as_client(fake),
        fact_set,
        post_format,
        limits,
        examples,
        angle=angle,
        revision=revision,
    )


async def test_short_post_is_one_part_from_a_single_call() -> None:
    fake = ScriptedLLMClient(single("Восьмого сентября 1380 года Мамай проиграл.", "F1", "F2"))

    draft = await write(fake)

    assert draft.post_format is PostFormat.SHORT
    assert draft.texts == ["Восьмого сентября 1380 года Мамай проиграл."]
    assert draft.rendered == draft.texts
    assert draft.used_fact_ids == ["F1", "F2"]
    assert draft.unverified_numbers == []
    assert draft.length_violations == []
    assert draft.attempts == 1
    [(messages, schema, max_tokens)] = fake.calls
    assert schema is SingleReply
    assert max_tokens > 0
    assert "at most 280 characters" in system_text(messages)


async def test_long_post_uses_the_long_limit() -> None:
    text = "Мамай проиграл. " * 100
    fake = ScriptedLLMClient(single(text, "F2"))

    draft = await write(fake, PostFormat.LONG)

    assert draft.post_format is PostFormat.LONG
    assert draft.texts == [text.strip()]
    assert draft.length_violations == []
    [(messages, schema, _)] = fake.calls
    assert schema is SingleReply
    assert "one long post" in system_text(messages)
    assert "at most 25000 characters" in system_text(messages)


async def test_thread_is_a_list_of_tweets() -> None:
    fake = ScriptedLLMClient(thread("Первый твит.", "Второй твит.", "Третий твит."))

    draft = await write(fake, PostFormat.THREAD)

    assert draft.post_format is PostFormat.THREAD
    assert draft.texts == ["Первый твит.", "Второй твит.", "Третий твит."]
    assert draft.attempts == 1
    [(messages, schema, _)] = fake.calls
    assert schema is ThreadReply
    assert "2 to 12 tweets" in system_text(messages)
    assert "at most 280 characters" in system_text(messages)


@pytest.mark.parametrize(
    "reply",
    [
        thread("Один твит."),
        thread("Первый.", "   "),
        single("   ", "F1"),
        {"text": "нет fact_ids"},
        "not json",
    ],
    ids=["one-tweet", "blank-tweet", "blank-text", "no-fact-ids", "not-json"],
)
async def test_invalid_reply_raises_invalid_response(reply: object) -> None:
    post_format = PostFormat.THREAD if "tweets" in str(reply) else PostFormat.SHORT
    with pytest.raises(LLMInvalidResponseError):
        await write(ScriptedLLMClient(reply), post_format)


async def test_llm_errors_propagate_unchanged() -> None:
    error = LLMUnavailableError("down")

    with pytest.raises(LLMUnavailableError) as raised:
        await write(ScriptedLLMClient(error))

    assert raised.value is error


async def test_empty_fact_set_is_rejected_before_calling_the_llm() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    with pytest.raises(ValueError, match="fact set"):
        await write(fake, fact_set=FactSet(topic=TOPIC, facts=[], disputes=[]))

    assert fake.calls == []


async def test_overlong_post_is_retried_once_with_the_exact_problem() -> None:
    long_text = "а" * 300
    fake = ScriptedLLMClient(single(long_text, "F1"), single("Короче.", "F2"))

    draft = await write(fake)

    assert draft.texts == ["Короче."]
    assert draft.used_fact_ids == ["F2"]
    assert draft.length_violations == []
    assert draft.attempts == 2
    first, second = (call[0] for call in fake.calls)
    assert second[: len(first)] == first
    assistant, correction = second[len(first) :]
    assert assistant.role is Role.ASSISTANT
    assert json.loads(assistant.content)["text"] == long_text
    assert correction.role is Role.USER
    assert "part 1: 300 characters, the limit is 280" in correction.content


async def test_post_still_overlong_after_the_retry_is_returned_whole_and_marked() -> None:
    first = "а" * 300
    second = "б" * 290
    fake = ScriptedLLMClient(single(first, "F1"), single(second, "F1"))

    draft = await write(fake)

    assert len(fake.calls) == 2
    assert draft.texts == [second]
    assert draft.attempts == 2
    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=290, limit=280)
    ]


async def test_post_exactly_at_the_limit_is_accepted() -> None:
    fake = ScriptedLLMClient(single("а" * 280, "F1"))

    draft = await write(fake)

    assert draft.length_violations == []
    assert len(fake.calls) == 1


async def test_only_the_overlong_tweet_is_named_in_the_correction() -> None:
    fake = ScriptedLLMClient(
        thread("Первый.", "б" * 281, "Третий."),
        thread("Первый.", "Второй.", "Третий."),
    )

    draft = await write(fake, PostFormat.THREAD)

    correction = fake.calls[1][0][-1].content
    assert "part 2: 281 characters, the limit is 280" in correction
    assert "part 1" not in correction
    assert "part 3" not in correction
    assert draft.length_violations == []


async def test_too_many_tweets_are_reported_and_never_cut() -> None:
    tweets = [f"Твит {index}." for index in range(1, 5)]
    fake = ScriptedLLMClient(thread(*tweets), thread(*tweets))

    draft = await write(fake, PostFormat.THREAD, make_limits(thread_max_tweets=3))

    assert "- 4 tweets, the maximum is 3" in fake.calls[1][0][-1].content
    assert draft.texts == tweets
    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_MANY_PARTS, actual=4, limit=3)
    ]


async def test_thread_numbering_is_off_by_default() -> None:
    fake = ScriptedLLMClient(thread("Первый.", "Второй."))

    draft = await write(fake, PostFormat.THREAD)

    assert [part.prefix for part in draft.parts] == ["", ""]
    assert draft.rendered == ["Первый.", "Второй."]
    assert "Do not number the tweets" in system_text(fake.calls[0][0])


async def test_thread_numbering_is_added_by_code_and_kept_out_of_the_text() -> None:
    fake = ScriptedLLMClient(thread("Первый.", "Второй."))

    draft = await write(fake, PostFormat.THREAD, make_limits(thread_numbering=True))

    assert draft.texts == ["Первый.", "Второй."]
    assert draft.rendered == ["1/ Первый.", "2/ Второй."]
    assert draft.unverified_numbers == []


async def test_thread_numbering_counts_against_the_tweet_limit() -> None:
    fits = "а" * 277
    overflows = "б" * 278
    fake = ScriptedLLMClient(thread(fits, overflows), thread(fits, overflows))

    draft = await write(fake, PostFormat.THREAD, make_limits(thread_numbering=True))

    assert "at most 276 characters" in system_text(fake.calls[0][0])
    assert "part 2: 278 characters, the limit is 277" in fake.calls[1][0][-1].content
    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=2, actual=281, limit=280)
    ]


async def test_short_and_long_posts_are_never_numbered() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    draft = await write(fake, limits=make_limits(thread_numbering=True))

    assert draft.rendered == ["Текст."]
    assert "at most 280 characters" in system_text(fake.calls[0][0])


def test_numbering_needs_room_in_the_tweet_limit() -> None:
    with pytest.raises(ValidationError, match="numbering"):
        make_limits(thread_tweet_max_chars=4, thread_numbering=True)

    assert make_limits(thread_tweet_max_chars=4).thread_tweet_max_chars == 4


async def test_unknown_and_repeated_fact_ids_are_dropped() -> None:
    fake = ScriptedLLMClient(single("Текст.", "f2", "[F1]", "F9", "S1", "F2", ""))

    draft = await write(fake)

    assert draft.used_fact_ids == ["F2", "F1"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Битва 8 сентября 1380 года.", []),
        ("Битва 8 сентября 1381 года.", ["1381"]),
        ("Около 60 000 или 150 000 человек.", []),
        ("Около 60000 или 150 000 человек.", []),
        ("Около 60,000 человек.", []),
        ("Около 70 000 человек в 1380 году.", ["70000"]),
        ("Было 3 полка и 12 воевод.", ["3", "12"]),
        ("Без единой цифры.", []),
    ],
    ids=[
        "from-a-fact",
        "invented-year",
        "from-disputed-facts",
        "no-separator",
        "comma-separator",
        "invented-count",
        "sorted-by-value",
        "no-numbers",
    ],
)
def test_numbers_are_checked_against_all_facts(text: str, expected: list[str]) -> None:
    assert unverified_numbers([text], FACT_SET) == expected


def test_a_range_matches_the_same_range_in_a_fact() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[make_fact("F1", "Храм строили в 1320–1330 годах.", DATE_QUOTE, FactStatus.SINGLE)],
        disputes=[],
    )

    assert unverified_numbers(["Его строили в 1320-1330 годах."], fact_set) == []
    assert unverified_numbers(["Его строили в 1320—1340 годах."], fact_set) == ["1340"]


def test_numbers_in_every_tweet_are_checked() -> None:
    assert unverified_numbers(["В 1380 году.", "Через 2 года."], FACT_SET) == ["2"]


async def test_unverified_numbers_are_a_warning_not_a_retry() -> None:
    fake = ScriptedLLMClient(single("Битва 1381 года.", "F1"))

    draft = await write(fake)

    assert draft.unverified_numbers == ["1381"]
    assert draft.attempts == 1
    assert len(fake.calls) == 1


async def test_a_number_from_the_topic_alone_is_unverified() -> None:
    fact_set = FACT_SET.model_copy(update={"topic": "Куликовская битва 600 лет спустя"})
    fake = ScriptedLLMClient(single("Через 600 лет о ней помнят.", "F1"))

    draft = await write(fake, fact_set=fact_set)

    assert draft.unverified_numbers == ["600"]
    assert "Куликовская битва 600 лет спустя" in user_text(fake.calls[0][0])


async def test_disputed_facts_are_never_in_the_block_of_facts_to_state() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake)

    prompt = user_text(fake.calls[0][0])
    stated, disputed = prompt.split(DISPUTES_HEADER)
    assert FACTS_HEADER in stated
    for text in (DATE_TEXT, WINNER_TEXT, PLACE_TEXT):
        assert text in stated
    for text in (ARMY_60_TEXT, ARMY_150_TEXT):
        assert text not in stated
        assert text in disputed
    assert DISPUTE_EXPLANATION in disputed
    assert "F4" not in stated
    assert "[confirmed]" in stated


async def test_a_fact_is_treated_as_disputed_by_status_or_by_group() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            make_fact("F1", DATE_TEXT, DATE_QUOTE, FactStatus.CONFIRMED),
            make_fact("F2", WINNER_TEXT, WINNER_QUOTE, FactStatus.SINGLE),
            make_fact("F3", PLACE_TEXT, PLACE_QUOTE, FactStatus.SINGLE),
            make_fact("F4", ARMY_60_TEXT, ARMY_60_QUOTE, FactStatus.DISPUTED),
        ],
        disputes=[Dispute(fact_ids=["F2", "F3", "F9"], explanation=DISPUTE_EXPLANATION)],
    )
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake, fact_set=fact_set)

    stated, disputed = user_text(fake.calls[0][0]).split(DISPUTES_HEADER)
    assert DATE_TEXT in stated
    for text in (WINNER_TEXT, PLACE_TEXT, ARMY_60_TEXT):
        assert text not in stated
        assert text in disputed


async def test_only_disputed_facts_leave_no_block_of_facts_to_state() -> None:
    fact_set = FactSet(
        topic=TOPIC,
        facts=[
            make_fact("F1", ARMY_60_TEXT, ARMY_60_QUOTE, FactStatus.DISPUTED),
            make_fact("F2", ARMY_150_TEXT, ARMY_150_QUOTE, FactStatus.DISPUTED),
        ],
        disputes=[Dispute(fact_ids=["F1", "F2"], explanation=DISPUTE_EXPLANATION)],
    )
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake, fact_set=fact_set)

    assert FACTS_HEADER not in user_text(fake.calls[0][0])


async def test_without_disputes_there_is_no_disputed_block() -> None:
    fact_set = FactSet(topic=TOPIC, facts=FACT_SET.facts[:3], disputes=[])
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake, fact_set=fact_set)

    assert DISPUTES_HEADER not in all_text(fake.calls[0][0])


async def test_quotes_and_urls_of_sources_never_reach_the_prompt() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake)

    prompt = all_text(fake.calls[0][0])
    for quote in (DATE_QUOTE, WINNER_QUOTE, PLACE_QUOTE, ARMY_60_QUOTE, ARMY_150_QUOTE):
        assert quote not in prompt
    assert "UNIQUE-QUOTE" not in prompt
    assert SOURCE_URL not in prompt


async def test_style_rules_come_from_the_shared_config() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake)

    system = system_text(fake.calls[0][0])
    for phrase in BANNED_PHRASES:
        assert phrase in system
    assert "по одним данным" in system


async def test_topic_is_a_frame_and_not_a_source() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake)

    messages = fake.calls[0][0]
    assert f"Topic (a frame, not a source): {TOPIC}" in user_text(messages)
    assert "It is a frame, not a source" in system_text(messages)


async def test_angle_and_revision_reach_the_prompt() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))
    revision = Revision(instruction="Сделай короче", previous=["Старый твит.", "Ещё один."])

    await write(fake, angle="  через судьбу Мамая ", revision=revision)

    prompt = user_text(fake.calls[0][0])
    assert "Angle requested by the author: через судьбу Мамая" in prompt
    assert "Revision requested by the author: Сделай короче" in prompt
    assert "[1]\nСтарый твит." in prompt
    assert "[2]\nЕщё один." in prompt


async def test_without_angle_or_revision_the_blocks_are_absent() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake, angle="   ")

    prompt = user_text(fake.calls[0][0])
    assert "Angle requested" not in prompt
    assert "Previous version" not in prompt
    assert "Revision requested" not in prompt


def test_revision_needs_an_instruction_and_a_previous_text() -> None:
    with pytest.raises(ValidationError):
        Revision(instruction="  ", previous=["Текст."])
    with pytest.raises(ValidationError):
        Revision(instruction="Короче", previous=[])


async def test_examples_are_shown_as_a_sample_of_rhythm() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake, examples=("Пример один.", "Пример два."))

    examples = [
        message.content
        for message in fake.calls[0][0]
        if message.role is Role.SYSTEM and "Reference posts" in message.content
    ]
    assert len(examples) == 1
    assert "Пример один." in examples[0]
    assert "Пример два." in examples[0]
    assert "Do not copy" in examples[0]


async def test_no_examples_means_no_examples_block() -> None:
    fake = ScriptedLLMClient(single("Текст.", "F1"))

    await write(fake)

    messages = fake.calls[0][0]
    assert "Reference posts" not in all_text(messages)
    assert [message.role for message in messages] == [Role.SYSTEM, Role.USER]


def test_a_single_post_has_exactly_one_part() -> None:
    parts = [DraftPart(text="Один."), DraftPart(text="Два.")]

    with pytest.raises(ValidationError, match="exactly one part"):
        Draft(
            post_format=PostFormat.SHORT,
            parts=parts,
            used_fact_ids=[],
            unverified_numbers=[],
            length_violations=[],
            attempts=1,
        )
