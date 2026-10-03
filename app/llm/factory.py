import asyncio

import httpx
from pydantic import SecretStr

from app.config.constants import API_KEY_ENV_NAMES, LLMProvider, LLMStep
from app.config.settings import Settings
from app.llm.anthropic import AnthropicClient
from app.llm.client import LLMClient
from app.llm.deepseek import DeepSeekClient
from app.llm.errors import LLMConfigError
from app.llm.target import CallTarget
from app.llm.transport import RetryingTransport, RetryPolicy, Sleep


def steps_by_provider(settings: Settings) -> dict[LLMProvider, list[LLMStep]]:
    steps: dict[LLMProvider, list[LLMStep]] = {}
    for step in LLMStep:
        steps.setdefault(settings.llm_route(step).provider, []).append(step)
    return steps


def validate_provider_keys(settings: Settings) -> None:
    problems = [
        f"{API_KEY_ENV_NAMES[provider]} is not set, "
        f"it is required by the steps that use {provider}: {', '.join(steps)}"
        for provider, steps in steps_by_provider(settings).items()
        if settings.api_key(provider) is None
    ]
    if problems:
        raise LLMConfigError("\n".join(problems))


def require_api_key(settings: Settings, provider: LLMProvider) -> SecretStr:
    key = settings.api_key(provider)
    if key is None:
        raise LLMConfigError(f"{API_KEY_ENV_NAMES[provider]} is not set")
    return key


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.llm_read_timeout_seconds, connect=settings.llm_connect_timeout_seconds
        )
    )


def build_retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_retries=settings.llm_max_retries,
        base_delay_seconds=settings.llm_retry_base_delay_seconds,
        max_delay_seconds=settings.llm_retry_max_delay_seconds,
        attempt_timeout_seconds=settings.llm_attempt_timeout_seconds,
    )


class LLMClientFactory:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        validate_provider_keys(settings)
        self._settings = settings
        self._transport = RetryingTransport(http_client, build_retry_policy(settings), sleep)

    def get_client(self, step: LLMStep) -> LLMClient:
        route = self._settings.llm_route(step)
        target = CallTarget(provider=route.provider, model=route.model, step=step)
        api_key = require_api_key(self._settings, route.provider)
        match route.provider:
            case LLMProvider.DEEPSEEK:
                return DeepSeekClient(
                    target=target,
                    transport=self._transport,
                    json_max_retries=self._settings.llm_json_max_retries,
                    base_url=self._settings.deepseek_base_url,
                    api_key=api_key,
                    thinking=self._settings.deepseek_thinking,
                )
            case LLMProvider.ANTHROPIC:
                return AnthropicClient(
                    target=target,
                    transport=self._transport,
                    json_max_retries=self._settings.llm_json_max_retries,
                    base_url=self._settings.anthropic_base_url,
                    api_key=api_key,
                    api_version=self._settings.anthropic_api_version,
                )
