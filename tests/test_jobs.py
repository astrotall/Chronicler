import asyncio
import logging
from collections.abc import Awaitable, Callable

import pytest
from app.bot.jobs import JobRunner

SECRET = "текст темы"


def waiting(event: asyncio.Event) -> Callable[[], Awaitable[None]]:
    async def wait() -> None:
        await event.wait()

    return wait


async def test_a_second_job_for_the_same_user_is_refused() -> None:
    runner = JobRunner()
    gate = asyncio.Event()

    assert runner.start(1, waiting(gate))
    assert not runner.start(1, waiting(gate))
    assert runner.start(2, waiting(gate))
    gate.set()
    await runner.wait_idle()

    assert not runner.busy(1)
    assert not runner.busy(2)


async def test_a_job_cancelled_before_it_starts_frees_the_user() -> None:
    runner = JobRunner()
    runner.start(1, waiting(asyncio.Event()))

    await runner.cancel_all()

    assert not runner.busy(1)
    assert runner.start(1, waiting(asyncio.Event()))
    await runner.cancel_all()


async def test_a_failing_job_is_logged_without_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    runner = JobRunner()

    async def fail() -> None:
        raise ValueError(SECRET)

    runner.start(1, fail)
    await runner.wait_idle()

    assert "ValueError" in caplog.text
    assert SECRET not in caplog.text
    assert not runner.busy(1)
