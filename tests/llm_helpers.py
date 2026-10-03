from collections.abc import Sequence

from app.domain.llm import LLMResult, Message
from app.llm.client import LLMClient
from app.llm.errors import LLMInvalidResponseError
from pydantic import BaseModel, ValidationError


class ScriptedLLMClient:
    def __init__(self, *replies: object) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[list[Message], type[BaseModel], int]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult:
        raise NotImplementedError

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        self.calls.append((list(messages), schema, max_tokens))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        try:
            return schema.model_validate(reply)
        except ValidationError as error:
            raise LLMInvalidResponseError("invalid json reply") from error


class RecordingLLMClient:
    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.prompts: list[list[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult:
        self.prompts.append(list(messages))
        return await self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        self.prompts.append(list(messages))
        return await self._inner.complete_json(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )


def as_client(fake: ScriptedLLMClient | RecordingLLMClient) -> LLMClient:
    return fake
