import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config.constants import CONTENT_TYPE_HEADER, JSON_CONTENT_TYPE, RETRY_AFTER_HEADER
from app.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMRequestError,
    LLMUnavailableError,
)
from app.llm.target import CallTarget

logger = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]

AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float
    attempt_timeout_seconds: float

    def backoff(self, attempt: int) -> float:
        return float(min(self.max_delay_seconds, self.base_delay_seconds * 2**attempt))


class ProviderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    headers: dict[str, str]
    content: str


class TransportResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: bytes
    retries: int


class ProviderErrorDetail(BaseModel):
    type: str | None = None
    message: str | None = None


class ProviderErrorBody(BaseModel):
    error: ProviderErrorDetail


def parse_retry_after(headers: Mapping[str, str], now: datetime) -> float | None:
    raw = headers.get(RETRY_AFTER_HEADER)
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - now).total_seconds())


def describe_status(response: httpx.Response) -> str:
    reason = f"HTTP {response.status_code}"
    try:
        detail = ProviderErrorBody.model_validate_json(response.content).error
    except ValidationError:
        return reason
    parts = [part for part in (detail.type, detail.message) if part]
    return f"{reason}: {': '.join(parts)}" if parts else reason


def error_for_status(response: httpx.Response, target: CallTarget) -> LLMError:
    reason = describe_status(response)
    status = response.status_code
    if status in AUTH_STATUSES:
        return LLMAuthError(reason, target)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return LLMRateLimitError(reason, target)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return LLMUnavailableError(reason, target)
    return LLMRequestError(reason, target)


def is_retryable(status: int) -> bool:
    return status == HTTPStatus.TOO_MANY_REQUESTS or status >= HTTPStatus.INTERNAL_SERVER_ERROR


class RetryingTransport:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        policy: RetryPolicy,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http_client = http_client
        self._policy = policy
        self._sleep = sleep

    async def post(self, request: ProviderRequest, target: CallTarget) -> TransportResponse:
        attempt = 0
        while True:
            try:
                async with asyncio.timeout(self._policy.attempt_timeout_seconds):
                    response = await self._http_client.post(
                        request.url,
                        headers={CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE, **request.headers},
                        content=request.content,
                    )
            except (httpx.HTTPError, TimeoutError) as error:
                failure: LLMError = LLMUnavailableError(
                    f"network error: {type(error).__name__}", target
                )
                failure.__cause__ = error
                delay = self._policy.backoff(attempt)
            else:
                if response.is_success:
                    return TransportResponse(content=response.content, retries=attempt)
                failure = error_for_status(response, target)
                if not is_retryable(response.status_code):
                    raise failure
                retry_after = parse_retry_after(response.headers, datetime.now(UTC))
                if retry_after is not None and retry_after > self._policy.max_delay_seconds:
                    raise failure
                delay = self._policy.backoff(attempt) if retry_after is None else retry_after
            if attempt >= self._policy.max_retries:
                raise failure
            logger.warning(
                "llm retry provider=%s model=%s step=%s attempt=%d reason=%s delay=%.2fs",
                target.provider,
                target.model,
                target.step,
                attempt + 1,
                failure.reason,
                delay,
            )
            await self._sleep(delay)
            attempt += 1
