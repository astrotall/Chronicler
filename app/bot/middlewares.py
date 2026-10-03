import logging
from collections.abc import Awaitable, Callable, Collection

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User

logger = logging.getLogger(__name__)

EVENT_USER_KEY = "event_from_user"


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_ids: Collection[int]) -> None:
        self._owner_ids = frozenset(owner_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, object]], Awaitable[object]],
        event: TelegramObject,
        data: dict[str, object],
    ) -> object:
        user = data.get(EVENT_USER_KEY)
        if isinstance(user, User) and user.id in self._owner_ids:
            return await handler(event, data)
        update_id = event.update_id if isinstance(event, Update) else None
        logger.info(
            "ignored update %s from non-owner user %s",
            update_id,
            user.id if isinstance(user, User) else None,
        )
        return None
