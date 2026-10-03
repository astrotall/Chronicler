from collections.abc import Sequence

from pydantic import BaseModel, Field, SecretStr

from app.config.constants import (
    DEEPSEEK_AUTHORIZATION_HEADER,
    DEEPSEEK_BEARER_PREFIX,
    DEEPSEEK_CHAT_PATH,
    DEEPSEEK_JSON_RESPONSE_FORMAT,
    DEEPSEEK_THINKING_DISABLED,
    DEEPSEEK_THINKING_ENABLED,
    DEEPSEEK_TRUNCATED_FINISH_REASON,
)
from app.domain.llm import LLMResult, LLMUsage, Message
from app.llm.base import BaseLLMClient
from app.llm.target import CallTarget
from app.llm.transport import ProviderRequest, RetryingTransport


class DeepSeekMessage(BaseModel):
    role: str
    content: str


class DeepSeekResponseFormat(BaseModel):
    type: str


class DeepSeekThinking(BaseModel):
    type: str


class DeepSeekRequest(BaseModel):
    model: str
    messages: list[DeepSeekMessage]
    max_tokens: int
    thinking: DeepSeekThinking
    temperature: float | None = None
    response_format: DeepSeekResponseFormat | None = None


class DeepSeekReplyMessage(BaseModel):
    content: str | None = None


class DeepSeekChoice(BaseModel):
    message: DeepSeekReplyMessage
    finish_reason: str | None = None


class DeepSeekUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class DeepSeekResponse(BaseModel):
    choices: list[DeepSeekChoice] = Field(min_length=1)
    usage: DeepSeekUsage


class DeepSeekClient(BaseLLMClient):
    def __init__(
        self,
        *,
        target: CallTarget,
        transport: RetryingTransport,
        json_max_retries: int,
        base_url: str,
        api_key: SecretStr,
        thinking: bool,
    ) -> None:
        super().__init__(target=target, transport=transport, json_max_retries=json_max_retries)
        self._url = f"{base_url.rstrip('/')}{DEEPSEEK_CHAT_PATH}"
        self._api_key = api_key
        self._thinking = thinking

    def _build_request(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None,
        max_tokens: int,
        json_mode: bool,
    ) -> ProviderRequest:
        body = DeepSeekRequest(
            model=self._target.model,
            messages=[
                DeepSeekMessage(role=message.role, content=message.content) for message in messages
            ],
            max_tokens=max_tokens,
            thinking=DeepSeekThinking(
                type=DEEPSEEK_THINKING_ENABLED if self._thinking else DEEPSEEK_THINKING_DISABLED
            ),
            temperature=None if self._thinking else temperature,
            response_format=(
                DeepSeekResponseFormat(type=DEEPSEEK_JSON_RESPONSE_FORMAT) if json_mode else None
            ),
        )
        return ProviderRequest(
            url=self._url,
            headers={
                DEEPSEEK_AUTHORIZATION_HEADER: (
                    f"{DEEPSEEK_BEARER_PREFIX}{self._api_key.get_secret_value()}"
                )
            },
            content=body.model_dump_json(exclude_none=True),
        )

    def _parse_response(self, content: bytes) -> LLMResult:
        response = DeepSeekResponse.model_validate_json(content)
        choice = response.choices[0]
        return LLMResult(
            text=choice.message.content or "",
            usage=LLMUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            ),
            truncated=choice.finish_reason == DEEPSEEK_TRUNCATED_FINISH_REASON,
        )
