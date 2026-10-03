from app.domain.draft import Draft, LengthIssue, LengthViolation, PostFormat
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Message, Role
from app.services.generator import WritingLimits, write_draft

from llm_helpers import ScriptedLLMClient, as_client

MIN_CHARS = 300
MIN_FACTS = 3
SOURCE_URL = "https://unique-size-source.example.org/day"

TOO_SHORT_TEXT = "а" * 100
EXPANDED_TEXT = "б" * 400
EXACT_MINIMUM_TEXT = "в" * MIN_CHARS
ONE_BELOW_MINIMUM_TEXT = "г" * (MIN_CHARS - 1)


def make_fact(fact_id: str, text: str, status: FactStatus = FactStatus.SINGLE) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[SourceRef(snippet_id="abc", url=SOURCE_URL, domain="example.org", quote=text)],
        status=status,
    )


FACT_SET = FactSet(
    topic="Обычный день",
    facts=[
        make_fact("F1", "Рабочий день начинался в семь утра."),
        make_fact("F2", "Многие семьи жили в коммунальных квартирах."),
        make_fact("F3", "Хлеб в начале десятилетия продавали по карточкам."),
        make_fact("F4", "Дети учились в школе шесть дней в неделю."),
        make_fact("F5", "Тираж газеты оценивают в 100 тысяч.", FactStatus.DISPUTED),
        make_fact("F6", "Тираж газеты составлял 200 тысяч.", FactStatus.DISPUTED),
    ],
    disputes=[Dispute(fact_ids=["F5", "F6"], explanation="Тиражи расходятся.")],
)


def make_limits(
    *, long_min_chars: int = MIN_CHARS, long_min_used_facts: int = MIN_FACTS
) -> WritingLimits:
    return WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        long_min_chars=long_min_chars,
        long_min_used_facts=long_min_used_facts,
    )


def single(text: str, *fact_ids: str) -> dict[str, object]:
    return {"fact_ids": list(fact_ids), "text": text}


async def write_long(fake: ScriptedLLMClient, limits: WritingLimits | None = None) -> Draft:
    return await write_draft(
        as_client(fake), FACT_SET, PostFormat.LONG, limits or make_limits(), ()
    )


def system_text(messages: list[Message]) -> str:
    return "\n".join(message.content for message in messages if message.role is Role.SYSTEM)


def correction(fake: ScriptedLLMClient, call: int) -> str:
    return fake.calls[call][0][-1].content


async def test_a_too_short_long_post_is_retried_with_the_size_and_the_unused_facts() -> None:
    fake = ScriptedLLMClient(single(TOO_SHORT_TEXT, "F1"), single(EXPANDED_TEXT, "F1", "F2", "F3"))

    draft = await write_long(fake)

    assert len(fake.calls) == 2
    assert draft.attempts == 2
    assert draft.texts == [EXPANDED_TEXT]
    assert draft.length_violations == []
    message = correction(fake, 1)
    assert "- 100 characters, the minimum is 300" in message
    assert "- 1 facts used, the minimum is 3" in message
    assert "expand the text and use more of the facts" in message
    assert "Facts not used yet: F2, F3, F4." in message
    assert "F5" not in message
    assert "F6" not in message


async def test_the_correction_does_not_list_unused_facts_when_every_fact_is_used() -> None:
    fake = ScriptedLLMClient(
        single(TOO_SHORT_TEXT, "F1", "F2", "F3", "F4"), single(EXPANDED_TEXT, "F1", "F2", "F3")
    )

    await write_long(fake)

    assert "Facts not used yet" not in correction(fake, 1)
    assert "- 100 characters, the minimum is 300" in correction(fake, 1)


async def test_a_long_post_that_stays_too_short_is_returned_with_both_violations() -> None:
    fake = ScriptedLLMClient(single(TOO_SHORT_TEXT, "F1"), single("д" * 150, "F1", "F2"))

    draft = await write_long(fake)

    assert len(fake.calls) == 2
    assert draft.attempts == 2
    assert draft.texts == ["д" * 150]
    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_SHORT, part=1, actual=150, limit=MIN_CHARS),
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=2, limit=MIN_FACTS),
    ]


async def test_the_exact_minimum_in_characters_passes_without_a_retry() -> None:
    fake = ScriptedLLMClient(single(EXACT_MINIMUM_TEXT, "F1", "F2", "F3"))

    draft = await write_long(fake)

    assert len(fake.calls) == 1
    assert draft.length_violations == []


async def test_one_character_below_the_minimum_is_a_size_violation_alone() -> None:
    fake = ScriptedLLMClient(
        single(ONE_BELOW_MINIMUM_TEXT, "F1", "F2", "F3"),
        single(ONE_BELOW_MINIMUM_TEXT, "F1", "F2", "F3"),
    )

    draft = await write_long(fake)

    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_SHORT, part=1, actual=MIN_CHARS - 1, limit=MIN_CHARS)
    ]


async def test_too_few_facts_is_a_violation_alone_when_the_text_is_long_enough() -> None:
    fake = ScriptedLLMClient(
        single(EXACT_MINIMUM_TEXT, "F1", "F2"), single(EXACT_MINIMUM_TEXT, "F1", "F2")
    )

    draft = await write_long(fake)

    assert draft.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=2, limit=MIN_FACTS)
    ]
    assert "- 2 facts used, the minimum is 3" in correction(fake, 1)
    assert "characters, the minimum" not in correction(fake, 1)


async def test_unknown_fact_ids_do_not_count_as_used_facts() -> None:
    fake = ScriptedLLMClient(
        single(EXACT_MINIMUM_TEXT, "F1", "F2", "F99"), single(EXACT_MINIMUM_TEXT, "F1", "F2", "F99")
    )

    draft = await write_long(fake)

    assert [violation.issue for violation in draft.length_violations] == [LengthIssue.TOO_FEW_FACTS]


async def test_the_fact_minimum_is_capped_by_the_facts_that_can_be_stated() -> None:
    limits = make_limits(long_min_used_facts=6)
    enough = ScriptedLLMClient(single(EXACT_MINIMUM_TEXT, "F1", "F2", "F3", "F4"))
    short_by_one = ScriptedLLMClient(
        single(EXACT_MINIMUM_TEXT, "F1", "F2", "F3"), single(EXACT_MINIMUM_TEXT, "F1", "F2", "F3")
    )

    passed = await write_long(enough, limits)
    failed = await write_long(short_by_one, limits)

    assert passed.length_violations == []
    assert failed.length_violations == [
        LengthViolation(issue=LengthIssue.TOO_FEW_FACTS, actual=3, limit=4)
    ]


async def test_a_disputed_fact_stated_by_its_numbers_counts_as_used() -> None:
    text = "ж" * 280 + " Тираж оценивают в 100 тысяч и в 200 тысяч."
    fake = ScriptedLLMClient(single(text, "F1", "F2"))

    draft = await write_long(fake)

    assert draft.used_fact_ids == ["F1", "F2", "F5", "F6"]
    assert draft.length_violations == []


async def test_the_minimum_is_off_when_the_limits_do_not_set_it() -> None:
    fake = ScriptedLLMClient(single("Коротко.", "F1"))

    draft = await write_long(fake, make_limits(long_min_chars=0, long_min_used_facts=0))

    assert len(fake.calls) == 1
    assert draft.length_violations == []
    assert "Make it as long as the facts deserve" in system_text(fake.calls[0][0])


async def test_short_and_thread_posts_ignore_the_long_minimum() -> None:
    short_fake = ScriptedLLMClient(single("Коротко.", "F1"))
    thread_fake = ScriptedLLMClient(
        {"fact_ids": ["F1"], "tweets": ["Первый твит.", "Второй твит."]}
    )
    limits = make_limits()

    short = await write_draft(as_client(short_fake), FACT_SET, PostFormat.SHORT, limits, ())
    thread = await write_draft(as_client(thread_fake), FACT_SET, PostFormat.THREAD, limits, ())

    assert len(short_fake.calls) == 1
    assert len(thread_fake.calls) == 1
    assert short.length_violations == []
    assert thread.length_violations == []
    assert "usually" not in system_text(short_fake.calls[0][0])
    assert "usually" not in system_text(thread_fake.calls[0][0])


async def test_the_long_prompt_states_the_size_the_paragraphs_and_the_facts() -> None:
    fake = ScriptedLLMClient(single("ж" * 1300, "F1", "F2", "F3", "F4"))
    limits = make_limits(long_min_chars=1200, long_min_used_facts=6)

    await write_long(fake, limits)

    prompt = system_text(fake.calls[0][0])
    assert "at least 3 paragraphs, usually 1200 to 2400 characters, never fewer than 1200" in prompt
    assert "Develop at least 4 of the facts you may state" in prompt
    assert "The limit of 25000 characters including spaces is a ceiling, not a target" in prompt
    assert "do not shrink it to a summary" in prompt
    assert "Make it as long as the facts deserve" not in prompt
