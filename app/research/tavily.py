import logging

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from app.config.constants import (
    AUTHORIZATION_HEADER,
    BEARER_PREFIX,
    CONTENT_TYPE_HEADER,
    HTTP_POST,
    JSON_CONTENT_TYPE,
    TAVILY_DEPTHS_WITHOUT_CHUNKS,
    TAVILY_SEARCH_URL,
    TavilySearchDepth,
)
from app.domain.snippet import Snippet, SnippetOrigin
from app.research.http import parse_response, send_request

logger = logging.getLogger(__name__)


class TavilySearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    search_depth: TavilySearchDepth
    max_results: int
    chunks_per_source: int | None = None


class TavilyResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""


class TavilyResponse(BaseModel):
    results: list[TavilyResult]


class TavilySource:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        search_depth: TavilySearchDepth,
        max_results: int,
        chunks_per_source: int,
    ) -> None:
        self.name = SnippetOrigin.TAVILY.value
        self._client = client
        self._api_key = api_key
        self._search_depth = search_depth
        self._max_results = max_results
        self._chunks_per_source = chunks_per_source

    async def search(self, query: str) -> list[Snippet]:
        request = self._client.build_request(
            HTTP_POST,
            TAVILY_SEARCH_URL,
            headers={
                AUTHORIZATION_HEADER: f"{BEARER_PREFIX}{self._api_key.get_secret_value()}",
                CONTENT_TYPE_HEADER: JSON_CONTENT_TYPE,
            },
            content=self._request_body(query),
        )
        content = await send_request(self.name, self._client, request)
        response = parse_response(self.name, TavilyResponse, content)
        snippets: list[Snippet] = []
        dropped = 0
        for result in response.results:
            try:
                snippets.append(
                    Snippet(
                        origin=SnippetOrigin.TAVILY,
                        title=result.title,
                        url=result.url,
                        text=result.content,
                    )
                )
            except ValidationError:
                dropped += 1
        if dropped:
            logger.warning("research source=%s dropped %d invalid results", self.name, dropped)
        return snippets

    def _request_body(self, query: str) -> str:
        chunks = (
            None if self._search_depth in TAVILY_DEPTHS_WITHOUT_CHUNKS else self._chunks_per_source
        )
        return TavilySearchRequest(
            query=query,
            search_depth=self._search_depth,
            max_results=self._max_results,
            chunks_per_source=chunks,
        ).model_dump_json(exclude_none=True)
