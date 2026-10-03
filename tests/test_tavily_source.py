import json
import logging

import httpx
import pytest
import respx
from app.config.constants import TavilySearchDepth
from app.domain.snippet import SnippetOrigin
from app.research.errors import (
    SourceAuthError,
    SourceInvalidResponseError,
    SourceRateLimitError,
    SourceRequestError,
    SourceUnavailableError,
)
from app.research.tavily import TavilySource
from pydantic import SecretStr

from research_helpers import TAVILY_TEST_KEY, fixture_json

URL = "https://api.tavily.com/search"
SECRET_BODY = "secret-provider-body"


def make_source(
    client: httpx.AsyncClient, depth: TavilySearchDepth = TavilySearchDepth.BASIC
) -> TavilySource:
    return TavilySource(
        client,
        SecretStr(TAVILY_TEST_KEY),
        search_depth=depth,
        max_results=5,
        chunks_per_source=3,
    )


def sent_body(route: respx.Route) -> dict[str, object]:
    body: dict[str, object] = json.loads(route.calls.last.request.content)
    return body


async def test_search_maps_results_to_snippets(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(
        return_value=httpx.Response(200, json=fixture_json("tavily_search.json"))
    )

    snippets = await make_source(http_client).search("Мамай золотая орда")

    assert [snippet.url for snippet in snippets] == [
        "https://example.org/history/mamai/",
        "https://example.com/articles/kulikovo#battle",
    ]
    assert all(snippet.origin is SnippetOrigin.TAVILY for snippet in snippets)
    assert all(snippet.lang is None for snippet in snippets)
    assert snippets[0].title == "Мамай: темник Золотой Орды"
    assert snippets[0].text.startswith("Мамай был темником")
    assert "[...]" in snippets[0].text


async def test_request_uses_bearer_auth_and_documented_body(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"results": []}))

    await make_source(http_client).search("Мамай")

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TAVILY_TEST_KEY}"
    assert request.headers["content-type"] == "application/json"
    assert sent_body(route) == {
        "query": "Мамай",
        "search_depth": "basic",
        "max_results": 5,
        "chunks_per_source": 3,
    }


@pytest.mark.parametrize(
    ("depth", "sends_chunks"),
    [
        (TavilySearchDepth.BASIC, True),
        (TavilySearchDepth.ADVANCED, True),
        (TavilySearchDepth.FAST, True),
        (TavilySearchDepth.ULTRA_FAST, False),
    ],
)
async def test_chunks_per_source_is_sent_only_where_it_applies(
    http_client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    depth: TavilySearchDepth,
    sends_chunks: bool,
) -> None:
    route = respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"results": []}))

    await make_source(http_client, depth).search("Мамай")

    body = sent_body(route)
    assert body["search_depth"] == depth.value
    assert ("chunks_per_source" in body) is sends_chunks


async def test_no_results_is_an_empty_list(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"results": []}))

    assert await make_source(http_client).search("Мамай") == []


async def test_results_without_url_or_text_are_dropped_with_a_warning(
    http_client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    results = [
        {"url": "", "title": "Пустой адрес", "content": "текст"},
        {"url": "https://example.org/empty", "title": "Пустой текст", "content": "  "},
        {"url": "https://example.org/no-content", "title": "Нет поля"},
        {"url": "not a url", "title": "Не адрес", "content": "текст"},
        {"url": "https://example.org/ok", "title": "Годный", "content": "текст"},
    ]
    respx_mock.post(URL).mock(return_value=httpx.Response(200, json={"results": results}))

    snippets = await make_source(http_client).search("Мамай")

    assert [snippet.url for snippet in snippets] == ["https://example.org/ok"]
    assert "dropped 4 invalid results" in caplog.text
    assert "Пустой адрес" not in caplog.text


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, SourceAuthError),
        (403, SourceAuthError),
        (429, SourceRateLimitError),
        (400, SourceRequestError),
        (432, SourceRequestError),
        (500, SourceUnavailableError),
        (502, SourceUnavailableError),
    ],
)
async def test_http_errors_map_to_typed_errors_without_leaking(
    http_client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    status: int,
    error: type[Exception],
) -> None:
    respx_mock.post(URL).mock(
        return_value=httpx.Response(status, json={"detail": {"error": SECRET_BODY}})
    )

    with pytest.raises(error) as raised:
        await make_source(http_client).search("Мамай")

    assert str(status) in str(raised.value)
    assert SECRET_BODY not in str(raised.value)
    assert TAVILY_TEST_KEY not in str(raised.value)


async def test_network_error_is_unavailable(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ReadTimeout("boom"))

    with pytest.raises(SourceUnavailableError) as raised:
        await make_source(http_client).search("Мамай")

    assert "ReadTimeout" in str(raised.value)
    assert TAVILY_TEST_KEY not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [f"<html>{SECRET_BODY}</html>", "{}", '{"results": "no"}', '{"results": [{"title": "t"}]}'],
    ids=["html", "no-results", "wrong-shape", "result-without-url"],
)
async def test_unexpected_body_is_an_invalid_response(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter, body: str
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text=body))

    with pytest.raises(SourceInvalidResponseError) as raised:
        await make_source(http_client).search("Мамай")

    assert SECRET_BODY not in str(raised.value)
