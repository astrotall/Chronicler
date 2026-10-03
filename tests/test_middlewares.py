import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, Update, User
from app.bot.middlewares import EVENT_USER_KEY, OwnerOnlyMiddleware

MESSAGE_DATE = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = 42
STRANGER_ID = 1000
SECRET_TEXT = "do not log this text"


def make_update(user_id: int) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    message = Message(
        message_id=1,
        date=MESSAGE_DATE,
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text=SECRET_TEXT,
    )
    return Update(update_id=7, message=message)


async def test_owner_passes_through() -> None:
    handler = AsyncMock(return_value="handled")
    middleware = OwnerOnlyMiddleware([OWNER_ID])
    update = make_update(OWNER_ID)
    data: dict[str, object] = {EVENT_USER_KEY: User(id=OWNER_ID, is_bot=False, first_name="Test")}

    result = await middleware(handler, update, data)

    assert result == "handled"
    handler.assert_awaited_once_with(update, data)


async def test_stranger_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    handler = AsyncMock()
    middleware = OwnerOnlyMiddleware([OWNER_ID])
    data: dict[str, object] = {
        EVENT_USER_KEY: User(id=STRANGER_ID, is_bot=False, first_name="Test")
    }

    with caplog.at_level(logging.INFO):
        result = await middleware(handler, make_update(STRANGER_ID), data)

    assert result is None
    handler.assert_not_awaited()
    assert len(caplog.records) == 1
    assert str(STRANGER_ID) in caplog.text
    assert SECRET_TEXT not in caplog.text


async def test_update_without_user_is_dropped() -> None:
    handler = AsyncMock()
    middleware = OwnerOnlyMiddleware([OWNER_ID])

    result = await middleware(handler, Update(update_id=8), {})

    assert result is None
    handler.assert_not_awaited()


async def test_empty_whitelist_drops_everyone() -> None:
    handler = AsyncMock()
    middleware = OwnerOnlyMiddleware([])
    data: dict[str, object] = {EVENT_USER_KEY: User(id=OWNER_ID, is_bot=False, first_name="Test")}

    await middleware(handler, make_update(OWNER_ID), data)

    handler.assert_not_awaited()
