import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.flow import BotContext, action_job, topic_job
from app.bot.keyboards import DraftCallback
from app.bot.messages import (
    BUSY_TEXT,
    INPUT_HINT_TEXT,
    STALE_BUTTONS_TEXT,
    START_TEXT,
    TOPIC_EMPTY_TEXT,
    TOPIC_TOO_LONG_TEMPLATE,
)
from app.bot.requests import TopicRejection, parse_topic
from app.config.constants import COMMAND_PREFIX, TOPIC_MAX_CHARS

logger = logging.getLogger(__name__)

REJECTION_TEXTS: dict[TopicRejection, str] = {
    TopicRejection.EMPTY: TOPIC_EMPTY_TEXT,
    TopicRejection.TOO_LONG: TOPIC_TOO_LONG_TEMPLATE.format(limit=TOPIC_MAX_CHARS),
}


def message_user_id(message: Message) -> int:
    return message.from_user.id if message.from_user is not None else message.chat.id


def query_chat_id(query: CallbackQuery) -> int:
    return query.message.chat.id if query.message is not None else query.from_user.id


async def handle_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def handle_hint(message: Message) -> None:
    await message.answer(INPUT_HINT_TEXT)


async def handle_topic(message: Message, bot: Bot, context: BotContext) -> None:
    request = parse_topic(message.text or "")
    if isinstance(request, TopicRejection):
        logger.info("topic rejected reason=%s", request)
        await message.answer(REJECTION_TEXTS[request])
        return
    logger.info("topic received topic_length=%d format=%s", len(request.topic), request.post_format)
    chat_id = message.chat.id
    started = context.jobs.start(
        message_user_id(message), lambda: topic_job(bot, chat_id, context, request)
    )
    if not started:
        await message.answer(BUSY_TEXT)


async def handle_action(
    query: CallbackQuery, callback_data: DraftCallback, bot: Bot, context: BotContext
) -> None:
    logger.info("button pressed action=%s", callback_data.action)
    chat_id = query_chat_id(query)
    started = context.jobs.start(
        query.from_user.id,
        lambda: action_job(bot, chat_id, context, callback_data.draft_id, callback_data.action),
    )
    await query.answer(None if started else BUSY_TEXT)


async def handle_unknown_callback(query: CallbackQuery) -> None:
    await query.answer(STALE_BUTTONS_TEXT, show_alert=True)


def build_router() -> Router:
    router = Router()
    router.message.register(handle_start, CommandStart())
    router.message.register(handle_hint, F.text.startswith(COMMAND_PREFIX))
    router.message.register(handle_topic, F.text)
    router.message.register(handle_hint)
    router.callback_query.register(handle_action, DraftCallback.filter())
    router.callback_query.register(handle_unknown_callback)
    return router
