import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def log_unexpected(error: BaseException) -> None:
    logger.error(
        "unexpected error=%s\n%s",
        type(error).__name__,
        "".join(traceback.format_tb(error.__traceback__)),
    )


class JobRunner:
    def __init__(self) -> None:
        self._active: dict[int, asyncio.Task[None]] = {}

    def busy(self, user_id: int) -> bool:
        return user_id in self._active

    def start(self, user_id: int, job: Callable[[], Awaitable[None]]) -> bool:
        if user_id in self._active:
            return False
        task = asyncio.create_task(self._run(job))
        self._active[user_id] = task
        task.add_done_callback(lambda finished: self._release(user_id, finished))
        return True

    def _release(self, user_id: int, task: asyncio.Task[None]) -> None:
        if self._active.get(user_id) is task:
            del self._active[user_id]

    async def wait_idle(self) -> None:
        while self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)
            await asyncio.sleep(0)

    async def cancel_all(self) -> None:
        for task in self._active.values():
            task.cancel()
        await self.wait_idle()

    async def _run(self, job: Callable[[], Awaitable[None]]) -> None:
        try:
            await job()
        except Exception as error:
            log_unexpected(error)
