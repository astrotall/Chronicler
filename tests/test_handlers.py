from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message
from app.bot.handlers import handle_start, handle_topic
from app.bot.messages import START_TEXT


def make_message(text: str | None) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.answer = AsyncMock()
    return message


async def test_start_replies_with_description() -> None:
    message = make_message("/start")

    await handle_start(message)

    message.answer.assert_awaited_once_with(START_TEXT)


async def test_topic_is_echoed_by_the_stub() -> None:
    message = make_message("Падение Константинополя")

    await handle_topic(message)

    message.answer.assert_awaited_once_with("got topic: Падение Константинополя")


async def test_topic_with_braces_is_echoed_verbatim() -> None:
    message = make_message("{topic} {0}")

    await handle_topic(message)

    message.answer.assert_awaited_once_with("got topic: {topic} {0}")
