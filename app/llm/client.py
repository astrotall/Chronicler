from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from app.domain.llm import LLMResult, Message


class LLMClient(Protocol):
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult: ...

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T: ...
