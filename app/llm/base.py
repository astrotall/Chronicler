import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from app.domain.llm import LLMResult, Message, Role
from app.llm.errors import LLMInvalidResponseError, LLMRequestError
from app.llm.json_reply import describe_validation_error, parse_reply, with_json_instruction
from app.llm.target import CallTarget
from app.llm.transport import ProviderRequest, RetryingTransport
from app.prompts.json_reply import render_json_correction

logger = logging.getLogger(__name__)

MILLISECONDS_PER_SECOND = 1000


class BaseLLMClient(ABC):
    def __init__(
        self, *, target: CallTarget, transport: RetryingTransport, json_max_retries: int
    ) -> None:
        self._target = target
        self._transport = transport
        self._json_max_retries = json_max_retries

    @property
    def target(self) -> CallTarget:
        return self._target

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult:
        result = await self._generate(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=False
        )
        if not result.text.strip():
            raise LLMInvalidResponseError("the reply has no text", self._target)
        return result

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        conversation = with_json_instruction(messages, schema)
        attempts = self._json_max_retries + 1
        for attempt in range(1, attempts + 1):
            result = await self._generate(
                conversation, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
            try:
                return parse_reply(result.text, schema)
            except ValidationError as error:
                problems = describe_validation_error(error)
            logger.warning(
                "llm invalid json provider=%s model=%s step=%s attempt=%d/%d "
                "problems=%d empty=%s truncated=%s",
                self._target.provider,
                self._target.model,
                self._target.step,
                attempt,
                attempts,
                len(problems),
                not result.text.strip(),
                result.truncated,
            )
            if result.text.strip():
                conversation = [
                    *conversation,
                    Message(role=Role.ASSISTANT, content=result.text),
                    render_json_correction(problems, truncated=result.truncated),
                ]
        raise LLMInvalidResponseError(
            f"no valid json for {schema.__name__} after {attempts} attempts", self._target
        )

    async def _generate(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None,
        max_tokens: int,
        json_mode: bool,
    ) -> LLMResult:
        if not any(message.role != Role.SYSTEM for message in messages):
            raise LLMRequestError("the request has no user message", self._target)
        request = self._build_request(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
        started = time.perf_counter()
        response = await self._transport.post(request, self._target)
        duration_ms = round((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
        try:
            result = self._parse_response(response.content)
        except ValidationError:
            raise LLMInvalidResponseError(
                "the provider response has an unexpected shape", self._target
            ) from None
        logger.info(
            "llm call provider=%s model=%s step=%s input_tokens=%d output_tokens=%d "
            "duration_ms=%d retries=%d truncated=%s",
            self._target.provider,
            self._target.model,
            self._target.step,
            result.usage.input_tokens,
            result.usage.output_tokens,
            duration_ms,
            response.retries,
            result.truncated,
        )
        return result

    @abstractmethod
    def _build_request(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None,
        max_tokens: int,
        json_mode: bool,
    ) -> ProviderRequest: ...

    @abstractmethod
    def _parse_response(self, content: bytes) -> LLMResult: ...
