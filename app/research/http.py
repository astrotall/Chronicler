from http import HTTPStatus

import httpx
from pydantic import BaseModel, ValidationError

from app.config.settings import Settings
from app.research.errors import (
    SourceAuthError,
    SourceError,
    SourceInvalidResponseError,
    SourceRateLimitError,
    SourceRequestError,
    SourceUnavailableError,
)

AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


def build_research_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.research_read_timeout_seconds,
            connect=settings.research_connect_timeout_seconds,
        )
    )


def error_for_status(source: str, status: int) -> SourceError:
    reason = f"HTTP {status}"
    if status in AUTH_STATUSES:
        return SourceAuthError(source, reason)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return SourceRateLimitError(source, reason)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return SourceUnavailableError(source, reason)
    return SourceRequestError(source, reason)


async def send_request(source: str, client: httpx.AsyncClient, request: httpx.Request) -> bytes:
    try:
        response = await client.send(request)
    except httpx.HTTPError as error:
        raise SourceUnavailableError(source, f"network error: {type(error).__name__}") from error
    if not response.is_success:
        raise error_for_status(source, response.status_code)
    return response.content


def parse_response[T: BaseModel](source: str, schema: type[T], content: bytes) -> T:
    try:
        return schema.model_validate_json(content)
    except ValidationError:
        raise SourceInvalidResponseError(source, "unexpected response body") from None
