import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    Sticker,
    Update,
    User,
    Voice,
)
from app.bot import messages
from app.bot.flow import BOT_CONTEXT_KEY, BotContext
from app.bot.keyboards import DraftCallback
from app.config.constants import TOPIC_MAX_CHARS
from app.config.settings import Settings
from app.domain.pipeline import PostAction
from app.llm.errors import LLMUnavailableError
from app.main import build_dispatcher, run
from app.research.errors import SourceUnavailableError
from app.services.pipeline import Pipeline
from app.services.run_store import InMemoryRunStore

from llm_helpers import ScriptedLLMClient
from pipeline_helpers import (
    CLEAN_CRITIC,
    POOR_CONFLICT,
    POOR_EXTRACTION,
    QUERIES,
    SHORT_TEXT,
    SHORTER_TEXT,
    SNIPPETS,
    THREAD_TWEETS,
    TOPIC,
    Clients,
    FakeSource,
    GatedLLMClient,
    make_limits,
    make_pipeline,
    short_reply,
    thread_reply,
)

DATE = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = 42
STRANGER_ID = 1000
TOKEN = "123456:test-token"


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self._next_id = 100

    async def __call__(
        self, method: TelegramMethod[object], request_timeout: int | None = None
    ) -> object:
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(
                message_id=self._next_id,
                date=DATE,
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        return True

    def of[M](self, kind: type[M]) -> list[M]:
        return [call for call in self.calls if isinstance(call, kind)]

    def sent(self) -> list[str]:
        return [call.text for call in self.of(SendMessage)]

    def last_text(self) -> str:
        texts = [
            call.text for call in self.calls if isinstance(call, SendMessage | EditMessageText)
        ]
        last = texts[-1]
        assert last is not None
        return last


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(Bot, "__call__", fake)
    return fake


def settings(monkeypatch: pytest.MonkeyPatch, timeout: float = 30) -> Settings:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", str(OWNER_ID))
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", str(timeout))
    return Settings(_env_file=None)


class Harness:
    def __init__(self, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher
        self.bot = Bot(token=TOKEN)
        self._update_id = 0

    @property
    def context(self) -> BotContext:
        context: BotContext = self.dispatcher[BOT_CONTEXT_KEY]
        return context

    async def feed(self, update_fields: dict[str, object]) -> None:
        self._update_id += 1
        await self.dispatcher.feed_update(
            self.bot, Update.model_validate({"update_id": self._update_id, **update_fields})
        )

    async def message(
        self,
        text: str | None = None,
        user_id: int = OWNER_ID,
        content: dict[str, object] | None = None,
    ) -> None:
        payload = Message.model_validate(
            {
                "message_id": 1,
                "date": DATE,
                "chat": Chat(id=user_id, type="private"),
                "from_user": User(id=user_id, is_bot=False, first_name="Test"),
                "text": text,
                **(content or {}),
            }
        )
        await self.feed({"message": payload})

    async def press(self, data: str, user_id: int = OWNER_ID) -> None:
        query = CallbackQuery(
            id=str(self._update_id),
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            chat_instance="chat",
            data=data,
            message=Message(
                message_id=5, date=DATE, chat=Chat(id=user_id, type="private"), text="пост"
            ),
        )
        await self.feed({"callback_query": query})

    async def idle(self) -> None:
        await self.context.jobs.wait_idle()


def harness(monkeypatch: pytest.MonkeyPatch, pipeline: Pipeline, timeout: float = 30) -> Harness:
    return Harness(build_dispatcher(settings(monkeypatch, timeout), pipeline))


def keyboard_callbacks(telegram: FakeTelegram) -> list[str]:
    markups = [
        call.reply_markup
        for call in telegram.of(SendMessage)
        if isinstance(call.reply_markup, InlineKeyboardMarkup)
    ]
    return [
        button.callback_data or ""
        for markup in markups
        for row in markup.inline_keyboard
        for button in row
    ]


def draft_id_of(telegram: FakeTelegram, action: PostAction) -> str:
    for data in reversed(keyboard_callbacks(telegram)):
        callback = DraftCallback.unpack(data)
        if callback.action is action:
            return data
    raise AssertionError("no such button")


async def test_a_topic_gets_a_post_facts_and_buttons(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message(TOPIC)
    await bot.idle()

    sends = telegram.of(SendMessage)
    assert sends[0].text == messages.STAGE_TEXTS[next(iter(messages.STAGE_TEXTS))]
    post, facts = sends[1], sends[2]
    assert post.text == SHORT_TEXT
    assert post.parse_mode is None
    assert isinstance(post.reply_markup, InlineKeyboardMarkup)
    assert facts.parse_mode == ParseMode.HTML
    assert "<b>F1</b>" in facts.text
    assert "<b>F6</b> · СПОРНО" in facts.text
    assert len(sends) == 3
    assert len(telegram.of(EditMessageText)) == 4
    [delete] = telegram.of(DeleteMessage)
    assert delete.message_id == 101
    labels = [button.text for row in post.reply_markup.inline_keyboard for button in row]
    assert labels == ["короче", "в тред", "другой заход", "ещё вариант"]


async def test_a_thread_topic_sends_one_message_per_tweet(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    pipeline = make_pipeline(Clients(writer=ScriptedLLMClient(thread_reply())))
    bot = harness(monkeypatch, pipeline)

    await bot.message(f"тред: {TOPIC}")
    await bot.idle()

    sends = telegram.of(SendMessage)
    assert [call.text for call in sends[1:4]] == THREAD_TWEETS
    assert [call.reply_markup is not None for call in sends[1:4]] == [False, False, True]
    assert "в тред" not in str(sends[3].reply_markup)
    assert sends[4].parse_mode == ParseMode.HTML


async def test_a_thread_with_too_few_facts_is_short_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    pipeline = make_pipeline(Clients(), limits=make_limits(thread_min_facts=6))
    bot = harness(monkeypatch, pipeline)

    await bot.message(f"тред: {TOPIC}")
    await bot.idle()

    sent = telegram.sent()
    assert sent[1] == SHORT_TEXT
    assert sent[2].startswith(messages.WARNINGS_HEADER)
    assert "нашлось 5" in sent[2]


@pytest.mark.parametrize(
    ("action", "reply", "expected"),
    [
        (PostAction.SHORTER, short_reply(SHORTER_TEXT), [SHORTER_TEXT]),
        (PostAction.THREAD, thread_reply(), THREAD_TWEETS),
        (PostAction.ANGLE, short_reply(SHORTER_TEXT), [SHORTER_TEXT]),
        (PostAction.VARIANT, short_reply(SHORTER_TEXT), [SHORTER_TEXT]),
    ],
)
async def test_a_button_sends_a_new_variant_without_new_research(
    monkeypatch: pytest.MonkeyPatch,
    telegram: FakeTelegram,
    action: PostAction,
    reply: object,
    expected: list[str],
) -> None:
    source = FakeSource("wiki", SNIPPETS)
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), reply),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )
    bot = harness(monkeypatch, make_pipeline(clients, [source]))
    await bot.message(TOPIC)
    await bot.idle()
    searches = source.calls
    before = len(telegram.calls)

    await bot.press(draft_id_of(telegram, action))
    await bot.idle()

    new_calls = telegram.calls[before:]
    assert isinstance(new_calls[0], AnswerCallbackQuery)
    assert new_calls[0].text is None
    sent = [call.text for call in new_calls if isinstance(call, SendMessage)]
    assert sent[1 : 1 + len(expected)] == expected
    assert sent[-1].startswith(messages.VARIANT_FACTS_HEADER_TEMPLATE.split(":")[0])
    assert source.calls == searches
    assert len(clients.critic.calls) == 2


async def test_a_variant_shows_only_its_facts(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), short_reply(SHORTER_TEXT, ["F1"])),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )
    bot = harness(monkeypatch, make_pipeline(clients))
    await bot.message(TOPIC)
    await bot.idle()

    await bot.press(draft_id_of(telegram, PostAction.VARIANT))
    await bot.idle()

    facts = telegram.sent()[-1]
    assert "<b>F1</b>" in facts
    assert "<b>F2</b>" not in facts
    assert facts.endswith(messages.OTHER_FACTS_TEMPLATE.format(count=6))


async def test_a_button_from_before_a_restart_says_the_buttons_are_stale(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    old = harness(monkeypatch, make_pipeline(Clients()))
    await old.message(TOPIC)
    await old.idle()
    data = draft_id_of(telegram, PostAction.VARIANT)
    restarted = harness(monkeypatch, make_pipeline(Clients()))

    await restarted.press(data)
    await restarted.idle()

    assert telegram.last_text() == messages.STALE_BUTTONS_TEXT


async def test_unknown_callback_data_is_answered_as_stale(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.press("garbage")
    await bot.press("d:unknown_action:abc")

    answers = telegram.of(AnswerCallbackQuery)
    assert [answer.text for answer in answers] == [messages.STALE_BUTTONS_TEXT] * 2
    assert all(answer.show_alert for answer in answers)


async def test_the_thread_button_with_too_few_facts_explains_itself(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients()
    store = InMemoryRunStore(20)
    before = harness(monkeypatch, make_pipeline(clients, store=store))
    await before.message(TOPIC)
    await before.idle()
    data = draft_id_of(telegram, PostAction.THREAD)
    stricter = make_pipeline(clients, store=store, limits=make_limits(thread_min_facts=9))
    bot = harness(monkeypatch, stricter)

    await bot.press(data)
    await bot.idle()

    assert telegram.last_text() == messages.THREAD_UNAVAILABLE_TEMPLATE.format(
        required=9, assertable=5
    )
    assert len(clients.writer.calls) == 1


async def test_a_second_topic_while_busy_is_refused_at_once(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    planner = GatedLLMClient(QUERIES)
    bot = harness(monkeypatch, make_pipeline(Clients(planner=planner)))

    await bot.message(TOPIC)
    await asyncio.wait_for(planner.entered.wait(), 1)
    await bot.message("Другая тема")
    await bot.press(DraftCallback(action=PostAction.VARIANT, draft_id="0" * 12).pack())

    assert bot.context.jobs.busy(OWNER_ID)
    assert messages.BUSY_TEXT in telegram.sent()
    assert [answer.text for answer in telegram.of(AnswerCallbackQuery)] == [messages.BUSY_TEXT]
    assert len(planner.calls) == 0
    planner.gate.set()
    await bot.idle()
    assert not bot.context.jobs.busy(OWNER_ID)
    assert SHORT_TEXT in telegram.sent()


async def test_a_double_press_runs_once(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    writer = GatedLLMClient(short_reply(), short_reply(SHORTER_TEXT))
    clients = Clients(writer=writer, critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC))
    bot = harness(monkeypatch, make_pipeline(clients))
    writer.gate.set()
    await bot.message(TOPIC)
    await bot.idle()
    data = draft_id_of(telegram, PostAction.SHORTER)
    writer.gate.clear()
    writer.entered.clear()

    await bot.press(data)
    await asyncio.wait_for(writer.entered.wait(), 1)
    await bot.press(data)
    writer.gate.set()
    await bot.idle()

    answers = [answer.text for answer in telegram.of(AnswerCallbackQuery)]
    assert answers == [None, messages.BUSY_TEXT]
    assert len(writer.calls) == 2
    assert telegram.sent().count(SHORTER_TEXT) == 1


async def test_a_run_over_the_timeout_is_cancelled_and_frees_the_owner(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    planner = GatedLLMClient()
    bot = harness(monkeypatch, make_pipeline(Clients(planner=planner)), timeout=0.05)

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.last_text() == messages.TIMEOUT_TEMPLATE.format(seconds=0)
    assert not bot.context.jobs.busy(OWNER_ID)
    await bot.message("Другая тема")
    assert messages.BUSY_TEXT not in telegram.sent()
    await bot.context.jobs.cancel_all()


async def test_an_unexpected_error_is_an_internal_error_message(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    clients = Clients(planner=ScriptedLLMClient(RuntimeError(f"secret {TOPIC}")))
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.last_text() == messages.INTERNAL_ERROR_TEXT
    assert "RuntimeError" in caplog.text
    assert TOPIC not in caplog.text
    assert not bot.context.jobs.busy(OWNER_ID)


async def test_a_step_failure_names_the_step(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients(extractor=ScriptedLLMClient(LLMUnavailableError("down")))
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.last_text() == "Шаг «выделение фактов» не удался: модель недоступна. " + (
        "Попробуй ещё раз позже."
    )
    assert not telegram.of(DeleteMessage)


async def test_too_few_facts_edits_the_progress_and_lists_the_facts(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients(extractor=ScriptedLLMClient(POOR_EXTRACTION, POOR_CONFLICT))
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(TOPIC)
    await bot.idle()

    edits = telegram.of(EditMessageText)
    assert edits[-1].text == messages.NOT_ENOUGH_FACTS_TEMPLATE.format(
        assertable=1, disputed=2, attributed=0, required=3
    )
    facts = telegram.of(SendMessage)[-1]
    assert facts.parse_mode == ParseMode.HTML
    assert "СПОРНО" in facts.text


async def test_all_sources_failing_is_said_plainly(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    source = FakeSource("tavily", error=SourceUnavailableError("tavily", "HTTP 503"))
    bot = harness(monkeypatch, make_pipeline(Clients(), [source]))

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.last_text().startswith("Источники не ответили: tavily (SourceUnavailableError)")


async def test_a_partial_failure_is_noted_under_the_facts(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    sources = [
        FakeSource("wiki", SNIPPETS),
        FakeSource("tavily", error=SourceUnavailableError("tavily", "HTTP 503")),
    ]
    bot = harness(monkeypatch, make_pipeline(Clients(), sources))

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.sent()[-1].endswith(
        "Не ответили источники: tavily (SourceUnavailableError) ×3."
    )
    assert "HTTP 503" not in telegram.sent()[-1]


async def test_no_sources_is_said_plainly(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    bot = harness(monkeypatch, make_pipeline(Clients(), []))

    await bot.message(TOPIC)
    await bot.idle()

    assert telegram.last_text() == messages.NO_SOURCES_TEXT


@pytest.mark.parametrize("text", ["/help", "/unknown topic", "/"])
async def test_an_unknown_command_gets_a_hint_and_no_run(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, text: str
) -> None:
    clients = Clients()
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(text)
    await bot.idle()

    assert telegram.sent() == [messages.INPUT_HINT_TEXT]
    assert clients.planner.calls == []


async def test_start_still_describes_the_bot(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message("/start")

    assert telegram.sent() == [messages.START_TEXT]


NON_TEXT: dict[str, dict[str, object]] = {
    "photo": {"photo": [PhotoSize(file_id="p", file_unique_id="p", width=1, height=1)]},
    "sticker": {
        "sticker": Sticker(
            file_id="s",
            file_unique_id="s",
            type="regular",
            width=1,
            height=1,
            is_animated=False,
            is_video=False,
        )
    },
    "voice": {"voice": Voice(file_id="v", file_unique_id="v", duration=1)},
    "document": {"document": Document(file_id="d", file_unique_id="d")},
}


@pytest.mark.parametrize("kind", list(NON_TEXT))
async def test_a_non_text_message_gets_a_hint(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, kind: str
) -> None:
    clients = Clients()
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(None, content=NON_TEXT[kind])

    assert telegram.sent() == [messages.INPUT_HINT_TEXT]
    assert clients.planner.calls == []


async def test_a_too_long_message_is_an_input_error(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients()
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message("я" * (TOPIC_MAX_CHARS + 1))
    await bot.idle()

    assert telegram.sent() == [messages.TOPIC_TOO_LONG_TEMPLATE.format(limit=TOPIC_MAX_CHARS)]
    assert clients.planner.calls == []


async def test_a_prefix_without_a_topic_is_an_input_error(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message("тред:")

    assert telegram.sent() == [messages.TOPIC_EMPTY_TEXT]


async def test_a_stranger_is_still_ignored(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram
) -> None:
    clients = Clients()
    bot = harness(monkeypatch, make_pipeline(clients))

    await bot.message(TOPIC, user_id=STRANGER_ID)
    await bot.message(None, user_id=STRANGER_ID, content=NON_TEXT["photo"])
    await bot.press("garbage", user_id=STRANGER_ID)
    await bot.idle()

    assert telegram.calls == []
    assert clients.planner.calls == []


async def test_the_topic_is_not_logged_by_the_bot(
    monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message(TOPIC)
    await bot.idle()

    assert TOPIC not in caplog.text
    assert SHORT_TEXT not in caplog.text


async def test_polling_handles_updates_as_tasks(
    monkeypatch: pytest.MonkeyPatch, llm_env: None
) -> None:
    monkeypatch.setenv("WIKIPEDIA_CONTACT", "owner@example.invalid")
    polling = AsyncMock()
    monkeypatch.setattr(Dispatcher, "start_polling", polling)

    await run(Settings(_env_file=None))

    polling.assert_awaited_once()
    assert polling.await_args is not None
    assert polling.await_args.kwargs["handle_as_tasks"] is True


class FlakyTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.retry_once_on: str | None = None
        self.fail_edits = False

    async def __call__(
        self, method: TelegramMethod[object], request_timeout: int | None = None
    ) -> object:
        raw = method
        if isinstance(method, SendMessage) and method.text == self.retry_once_on:
            self.retry_once_on = None
            self.calls.append(raw)
            raise TelegramRetryAfter(method=raw, message="flood", retry_after=0)
        if isinstance(method, EditMessageText | DeleteMessage) and self.fail_edits:
            self.calls.append(raw)
            raise TelegramBadRequest(method=raw, message="message is not modified")
        return await super().__call__(method, request_timeout)


@pytest.fixture
def flaky(monkeypatch: pytest.MonkeyPatch) -> FlakyTelegram:
    fake = FlakyTelegram()
    monkeypatch.setattr(Bot, "__call__", fake)
    return fake


async def test_a_flood_wait_while_sending_is_waited_out_once(
    monkeypatch: pytest.MonkeyPatch, flaky: FlakyTelegram
) -> None:
    flaky.retry_once_on = SHORT_TEXT
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message(TOPIC)
    await bot.idle()

    assert flaky.sent().count(SHORT_TEXT) == 2
    assert flaky.of(DeleteMessage)
    assert messages.INTERNAL_ERROR_TEXT not in flaky.sent()


async def test_a_failing_progress_edit_does_not_stop_the_post(
    monkeypatch: pytest.MonkeyPatch, flaky: FlakyTelegram
) -> None:
    flaky.fail_edits = True
    bot = harness(monkeypatch, make_pipeline(Clients()))

    await bot.message(TOPIC)
    await bot.idle()

    assert SHORT_TEXT in flaky.sent()
    assert flaky.sent()[-1].startswith("<b>Факты</b>")


async def test_a_failure_status_is_sent_anew_when_the_progress_cannot_be_edited(
    monkeypatch: pytest.MonkeyPatch, flaky: FlakyTelegram
) -> None:
    flaky.fail_edits = True
    bot = harness(monkeypatch, make_pipeline(Clients(), []))

    await bot.message(TOPIC)
    await bot.idle()

    assert flaky.sent()[-1] == messages.NO_SOURCES_TEXT
