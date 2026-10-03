from collections.abc import Sequence

from pydantic import BaseModel, Field, SecretStr

from app.config.constants import (
    ANTHROPIC_API_KEY_HEADER,
    ANTHROPIC_MESSAGES_PATH,
    ANTHROPIC_SYSTEM_SEPARATOR,
    ANTHROPIC_TEXT_BLOCK_TYPE,
    ANTHROPIC_TRUNCATED_STOP_REASON,
    ANTHROPIC_VERSION_HEADER,
)
from app.domain.llm import LLMResult, LLMUsage, Message, Role
from app.llm.base import BaseLLMClient
from app.llm.target import CallTarget
from app.llm.transport import ProviderRequest, RetryingTransport


class AnthropicMessage(BaseModel):
    role: str
    content: str


class AnthropicRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[AnthropicMessage]
    system: str | None = None


class AnthropicContentBlock(BaseModel):
    type: str
    text: str | None = None


class AnthropicUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class AnthropicResponse(BaseModel):
    content: list[AnthropicContentBlock]
    stop_reason: str | None = None
    usage: AnthropicUsage


class AnthropicClient(BaseLLMClient):
    def __init__(
        self,
        *,
        target: CallTarget,
        transport: RetryingTransport,
        json_max_retries: int,
        base_url: str,
        api_key: SecretStr,
        api_version: str,
    ) -> None:
        super().__init__(target=target, transport=transport, json_max_retries=json_max_retries)
        self._url = f"{base_url.rstrip('/')}{ANTHROPIC_MESSAGES_PATH}"
        self._api_key = api_key
        self._api_version = api_version

    def _build_request(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None,
        max_tokens: int,
        json_mode: bool,
    ) -> ProviderRequest:
        system_parts = [message.content for message in messages if message.role == Role.SYSTEM]
        body = AnthropicRequest(
            model=self._target.model,
            max_tokens=max_tokens,
            messages=[
                AnthropicMessage(role=message.role, content=message.content)
                for message in messages
                if message.role != Role.SYSTEM
            ],
            system=ANTHROPIC_SYSTEM_SEPARATOR.join(system_parts) if system_parts else None,
        )
        return ProviderRequest(
            url=self._url,
            headers={
                ANTHROPIC_API_KEY_HEADER: self._api_key.get_secret_value(),
                ANTHROPIC_VERSION_HEADER: self._api_version,
            },
            content=body.model_dump_json(exclude_none=True),
        )

    def _parse_response(self, content: bytes) -> LLMResult:
        response = AnthropicResponse.model_validate_json(content)
        text = "".join(
            block.text
            for block in response.content
            if block.type == ANTHROPIC_TEXT_BLOCK_TYPE and block.text is not None
        )
        return LLMResult(
            text=text,
            usage=LLMUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            truncated=response.stop_reason == ANTHROPIC_TRUNCATED_STOP_REASON,
        )
