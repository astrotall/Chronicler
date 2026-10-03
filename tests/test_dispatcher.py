from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User
from app.bot.messages import START_TEXT
from app.config.settings import Settings
from app.main import build_dispatcher

from pipeline_helpers import Clients, make_pipeline

MESSAGE_DATE = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = 42
STRANGER_ID = 1000
FAKE_TOKEN = "123456:test-token"


def make_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", str(OWNER_ID))
    return Settings(_env_file=None)


def make_update(user_id: int, text: str) -> Update:
    message = Message(
        message_id=1,
        date=MESSAGE_DATE,
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )
    return Update(update_id=1, message=message)


@pytest.fixture
def bot_call(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    call = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", call)
    return call


async def test_owner_gets_a_reply(monkeypatch: pytest.MonkeyPatch, bot_call: AsyncMock) -> None:
    dispatcher = build_dispatcher(make_settings(monkeypatch), make_pipeline(Clients()))
    bot = Bot(token=FAKE_TOKEN)

    await dispatcher.feed_update(bot, make_update(OWNER_ID, "/start"))

    bot_call.assert_awaited_once()
    method = bot_call.await_args_list[0].args[0]
    assert isinstance(method, SendMessage)
    assert method.chat_id == OWNER_ID
    assert method.text == START_TEXT


async def test_stranger_gets_no_reply(monkeypatch: pytest.MonkeyPatch, bot_call: AsyncMock) -> None:
    dispatcher = build_dispatcher(make_settings(monkeypatch), make_pipeline(Clients()))
    bot = Bot(token=FAKE_TOKEN)

    await dispatcher.feed_update(bot, make_update(STRANGER_ID, "/start"))
    await dispatcher.feed_update(bot, make_update(STRANGER_ID, "any topic"))

    bot_call.assert_not_awaited()
