import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NamedTuple

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import LinkPreviewOptions

from app.bot.formatting import (
    OutgoingMessage,
    Rendered,
    render_rework_outcome,
    render_topic_outcome,
)
from app.bot.jobs import JobRunner, log_unexpected
from app.bot.messages import INTERNAL_ERROR_TEXT, STAGE_TEXTS, TIMEOUT_TEMPLATE
from app.bot.progress import ProgressMessage
from app.bot.requests import TopicRequest
from app.config.constants import TELEGRAM_SEND_RETRIES
from app.domain.pipeline import PipelineStage, PostAction
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)

BOT_CONTEXT_KEY = "context"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class BotContext(NamedTuple):
    pipeline: Pipeline
    jobs: JobRunner
    timeout_seconds: float


async def send(bot: Bot, chat_id: int, message: OutgoingMessage) -> None:
    for attempt in range(TELEGRAM_SEND_RETRIES + 1):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message.text,
                parse_mode=ParseMode.HTML if message.html else None,
                reply_markup=message.keyboard,
                link_preview_options=NO_PREVIEW,
            )
        except TelegramRetryAfter as error:
            if attempt == TELEGRAM_SEND_RETRIES:
                raise
            logger.warning("telegram asked to wait seconds=%d", error.retry_after)
            await asyncio.sleep(error.retry_after)
        else:
            return


async def deliver(bot: Bot, chat_id: int, progress: ProgressMessage, rendered: Rendered) -> None:
    if rendered.status is not None:
        await progress.fail(rendered.status)
    for message in rendered.messages:
        await send(bot, chat_id, message)
    if rendered.status is None:
        await progress.close()


async def run_job[T](
    bot: Bot,
    chat_id: int,
    timeout_seconds: float,
    first_stage: PipelineStage,
    call: Callable[[ProgressMessage], Awaitable[T]],
    render: Callable[[T], Rendered],
) -> None:
    progress = ProgressMessage(bot, chat_id)
    await progress.show(STAGE_TEXTS[first_stage])
    try:
        async with asyncio.timeout(timeout_seconds):
            outcome = await call(progress)
        rendered = render(outcome)
    except TimeoutError:
        logger.warning("job timed out timeout_seconds=%s", timeout_seconds)
        await progress.fail(TIMEOUT_TEMPLATE.format(seconds=round(timeout_seconds)))
        return
    except Exception as error:
        log_unexpected(error)
        await progress.fail(INTERNAL_ERROR_TEXT)
        return
    try:
        await deliver(bot, chat_id, progress, rendered)
    except Exception as error:
        log_unexpected(error)
        await progress.fail(INTERNAL_ERROR_TEXT)


async def topic_job(bot: Bot, chat_id: int, context: BotContext, request: TopicRequest) -> None:
    await run_job(
        bot,
        chat_id,
        context.timeout_seconds,
        PipelineStage.PLANNING,
        lambda progress: context.pipeline.run_topic(
            request.topic, request.post_format, progress.stage
        ),
        render_topic_outcome,
    )


async def action_job(
    bot: Bot, chat_id: int, context: BotContext, draft_id: str, action: PostAction
) -> None:
    await run_job(
        bot,
        chat_id,
        context.timeout_seconds,
        PipelineStage.WRITING,
        lambda progress: context.pipeline.rework(draft_id, action, progress.stage),
        render_rework_outcome,
    )
