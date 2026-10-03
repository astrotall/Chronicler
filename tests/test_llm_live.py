import pytest
from app.config.constants import (
    API_KEY_ENV_NAMES,
    ENV_FILE,
    ENV_FILE_ENCODING,
    LLMProvider,
    LLMStep,
)
from app.config.settings import Settings
from app.domain.llm import Message, Role
from app.llm.factory import LLMClientFactory, build_http_client
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_TOKENS = 2000


class LiveKeys(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding=ENV_FILE_ENCODING, extra="ignore"
    )

    deepseek_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None


class Capital(BaseModel):
    country: str
    city: str


def has_live_key(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())


def skip_without_key(provider: LLMProvider, key: SecretStr | None) -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        not has_live_key(key), reason=f"{API_KEY_ENV_NAMES[provider]} is not set"
    )


KEYS = LiveKeys()
PROVIDERS = pytest.mark.parametrize(
    "provider",
    [
        pytest.param(
            provider,
            marks=[pytest.mark.integration, skip_without_key(provider, key)],
        )
        for provider, key in (
            (LLMProvider.DEEPSEEK, KEYS.deepseek_api_key),
            (LLMProvider.ANTHROPIC, KEYS.anthropic_api_key),
        )
    ],
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (None, False),
        (SecretStr(""), False),
        (SecretStr("  \t\n"), False),
        (SecretStr("sk-live-key"), True),
    ],
    ids=["none", "empty", "whitespace", "set"],
)
def test_has_live_key(key: SecretStr | None, expected: bool) -> None:
    assert has_live_key(key) is expected


def live_settings(provider: LLMProvider) -> Settings:
    return Settings(
        telegram_bot_token="live-test",
        owner_telegram_ids=[1],
        deepseek_api_key=KEYS.deepseek_api_key,
        anthropic_api_key=KEYS.anthropic_api_key,
        llm_query_planning_provider=provider,
        llm_fact_extraction_provider=provider,
        llm_writing_provider=provider,
        llm_style_critique_provider=provider,
    )


@PROVIDERS
async def test_live_complete(provider: LLMProvider) -> None:
    current = live_settings(provider)
    async with build_http_client(current) as http_client:
        client = LLMClientFactory(current, http_client).get_client(LLMStep.WRITING)

        result = await client.complete(
            [Message(role=Role.USER, content="Ответь одним словом: столица Франции?")],
            max_tokens=MAX_TOKENS,
        )

    assert "париж" in result.text.lower()
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0


@PROVIDERS
async def test_live_complete_json(provider: LLMProvider) -> None:
    current = live_settings(provider)
    async with build_http_client(current) as http_client:
        client = LLMClientFactory(current, http_client).get_client(LLMStep.FACT_EXTRACTION)

        capital = await client.complete_json(
            [Message(role=Role.USER, content="Назови столицу Италии.")],
            Capital,
            max_tokens=MAX_TOKENS,
        )

    assert "рим" in capital.city.lower() or "rome" in capital.city.lower()
