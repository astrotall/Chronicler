from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.messages import START_TEXT, TOPIC_STUB_TEMPLATE


async def handle_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def handle_topic(message: Message) -> None:
    await message.answer(TOPIC_STUB_TEMPLATE.format(topic=message.text))


def build_router() -> Router:
    router = Router()
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_topic, F.text)
    return router
