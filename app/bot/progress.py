import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot.messages import STAGE_TEXTS
from app.domain.pipeline import PipelineStage

logger = logging.getLogger(__name__)


class ProgressMessage:
    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._message_id: int | None = None
        self._text: str | None = None

    async def show(self, text: str) -> bool:
        if text == self._text:
            return True
        try:
            if self._message_id is None:
                sent = await self._bot.send_message(chat_id=self._chat_id, text=text)
                self._message_id = sent.message_id
            else:
                await self._bot.edit_message_text(
                    text=text, chat_id=self._chat_id, message_id=self._message_id
                )
        except TelegramAPIError as error:
            logger.warning("progress update failed error=%s", type(error).__name__)
            return False
        self._text = text
        return True

    async def stage(self, stage: PipelineStage) -> None:
        await self.show(STAGE_TEXTS[stage])

    async def fail(self, text: str) -> None:
        if await self.show(text):
            return
        self._message_id = None
        await self.show(text)

    async def close(self) -> None:
        if self._message_id is None:
            return
        try:
            await self._bot.delete_message(chat_id=self._chat_id, message_id=self._message_id)
        except TelegramAPIError as error:
            logger.warning("progress delete failed error=%s", type(error).__name__)
        self._message_id = None
        self._text = None
