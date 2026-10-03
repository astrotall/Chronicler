from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message
from app.bot.handlers import handle_hint, handle_start
from app.bot.messages import INPUT_HINT_TEXT, START_TEXT


def make_message(text: str | None) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.answer = AsyncMock()
    return message


async def test_start_replies_with_description() -> None:
    message = make_message("/start")

    await handle_start(message)

    message.answer.assert_awaited_once_with(START_TEXT)


async def test_the_hint_shows_how_to_send_a_topic() -> None:
    message = make_message(None)

    await handle_hint(message)

    message.answer.assert_awaited_once_with(INPUT_HINT_TEXT)
