import os
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from app.config.settings import Settings
from app.llm.factory import LLMClientFactory

LLM_ENV_PREFIXES = ("LLM_", "DEEPSEEK_", "ANTHROPIC_")
FAKE_DEEPSEEK_KEY = "sk-deepseek-test-key"
FAKE_ANTHROPIC_KEY = "sk-ant-test-key"


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(LLM_ENV_PREFIXES):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_DEEPSEEK_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def make_factory(
    llm_env: None, http_client: httpx.AsyncClient, sleeps: list[float]
) -> Callable[[], LLMClientFactory]:
    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def build() -> LLMClientFactory:
        return LLMClientFactory(Settings(_env_file=None), http_client, sleep=record_sleep)

    return build
