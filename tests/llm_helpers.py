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


def as_client(fake: ScriptedLLMClient) -> LLMClient:
    return fake
