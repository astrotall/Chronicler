import json
import logging

import httpx
import pytest
import respx
from app.config.constants import WikipediaLanguage
from app.domain.snippet import SnippetOrigin
from app.research.errors import (
    SourceAuthError,
    SourceInvalidResponseError,
    SourceRateLimitError,
    SourceRequestError,
    SourceUnavailableError,
)
from app.research.wikipedia import WikipediaSource, clean_extract

from research_helpers import CONTACT, fixture_json

RU_URL = "https://ru.wikipedia.org/w/api.php"
EN_URL = "https://en.wikipedia.org/w/api.php"
MAX_ARTICLES = 2
MAX_CHARS = 6000
SECRET_BODY = "secret-provider-body"


def make_source(
    client: httpx.AsyncClient,
    language: WikipediaLanguage = WikipediaLanguage.RU,
    max_chars: int = MAX_CHARS,
) -> WikipediaSource:
    return WikipediaSource(client, language, CONTACT, MAX_ARTICLES, max_chars)


def serve(
    respx_mock: respx.MockRouter,
    url: str,
    search: dict[str, object],
    articles: dict[int, dict[str, object]],
) -> respx.Route:
    def handler(request: httpx.Request) -> httpx.Response:
        if "list" in request.url.params:
            return httpx.Response(200, json=search)
        return httpx.Response(200, json=articles[int(request.url.params["pageids"])])

    return respx_mock.get(url).mock(side_effect=handler)


def serve_ru(respx_mock: respx.MockRouter) -> respx.Route:
    return serve(
        respx_mock,
        RU_URL,
        fixture_json("wikipedia_ru_search.json"),
        {
            118877: fixture_json("wikipedia_ru_article.json"),
            3119654: fixture_json("wikipedia_ru_disambiguation.json"),
        },
    )


async def test_ru_search_returns_one_snippet_per_article(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    serve_ru(respx_mock)

    snippets = await make_source(http_client).search("Мамай")

    assert len(snippets) == 1
    snippet = snippets[0]
    assert snippet.origin is SnippetOrigin.WIKIPEDIA_RU
    assert snippet.lang == "ru"
    assert snippet.title == "Мамай"
    assert snippet.url.startswith("https://ru.wikipedia.org/wiki/")
    assert snippet.text.startswith("Мамай (на рубеже 1320")
    assert "== Биография ==" in snippet.text


async def test_ru_text_has_no_stress_marks_and_no_service_sections(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    serve_ru(respx_mock)

    snippet = (await make_source(http_client).search("Мамай"))[0]

    assert "́" not in snippet.text
    for heading in ("== См. также ==", "== Примечания ==", "== Литература ==", "== Ссылки =="):
        assert heading not in snippet.text
    assert snippet.text.rstrip().splitlines()[-1] != "== Память в языке =="


async def test_disambiguation_page_is_skipped(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = serve(
        respx_mock,
        RU_URL,
        fixture_json("wikipedia_ru_search.json"),
        {
            118877: fixture_json("wikipedia_ru_disambiguation.json"),
            3119654: fixture_json("wikipedia_ru_disambiguation.json"),
        },
    )

    assert await make_source(http_client).search("Мамай") == []
    assert route.call_count == 1 + MAX_ARTICLES


async def test_en_search_uses_the_english_edition(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    serve(
        respx_mock,
        EN_URL,
        fixture_json("wikipedia_en_search.json"),
        {
            241828: fixture_json("wikipedia_en_article.json"),
            17712889: {"batchcomplete": True, "query": {"pages": [{"pageid": 17712889}]}},
        },
    )

    snippets = await make_source(http_client, WikipediaLanguage.EN).search("Mamai")

    assert len(snippets) == 1
    assert snippets[0].origin is SnippetOrigin.WIKIPEDIA_EN
    assert snippets[0].lang == "en"
    assert snippets[0].url.startswith("https://en.wikipedia.org/wiki/")
    assert "== Origins ==" in snippets[0].text
    assert "== References ==" not in snippets[0].text
    assert "== Bibliography ==" not in snippets[0].text


async def test_requests_carry_the_documented_parameters_and_user_agent(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = serve_ru(respx_mock)

    await make_source(http_client).search("Мамай и Орда")

    search_request, first_article_request = route.calls[0].request, route.calls[1].request
    search_params = search_request.url.params
    assert search_params["action"] == "query"
    assert search_params["list"] == "search"
    assert search_params["srsearch"] == "Мамай и Орда"
    assert search_params["srlimit"] == str(MAX_ARTICLES)
    assert search_params["srnamespace"] == "0"
    assert search_params["format"] == "json"
    assert search_params["formatversion"] == "2"
    article_params = first_article_request.url.params
    assert article_params["prop"] == "extracts|info|pageprops"
    assert article_params["pageids"] == "118877"
    assert article_params["explaintext"] == "1"
    assert article_params["exsectionformat"] == "wiki"
    assert article_params["ppprop"] == "disambiguation"
    assert "exintro" not in article_params
    user_agent = search_request.headers["user-agent"]
    assert user_agent.startswith("ChroniclerBot/")
    assert f"({CONTACT})" in user_agent
    assert "httpx/" in user_agent


async def test_no_hits_make_no_article_requests(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(RU_URL).mock(
        return_value=httpx.Response(200, json={"batchcomplete": True, "query": {"search": []}})
    )

    assert await make_source(http_client).search("ничего") == []
    assert route.call_count == 1


async def test_page_without_extract_is_skipped(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    missing: dict[str, object] = {
        "batchcomplete": True,
        "query": {"pages": [{"title": "Нет", "missing": True}]},
    }
    serve(
        respx_mock,
        RU_URL,
        fixture_json("wikipedia_ru_search.json"),
        {118877: missing, 3119654: missing},
    )

    assert await make_source(http_client).search("Мамай") == []


async def test_extract_limit_is_applied(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    serve_ru(respx_mock)

    snippet = (await make_source(http_client, max_chars=800).search("Мамай"))[0]

    assert len(snippet.text) <= 800
    assert snippet.text.startswith("Мамай (на рубеже 1320")


async def test_api_error_in_a_200_response_is_a_request_error(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(RU_URL).mock(
        return_value=httpx.Response(
            200, json={"error": {"code": "ratelimited", "info": SECRET_BODY}}
        )
    )

    with pytest.raises(SourceRequestError) as raised:
        await make_source(http_client).search("Мамай")

    assert "ratelimited" in str(raised.value)
    assert SECRET_BODY not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, SourceAuthError),
        (429, SourceRateLimitError),
        (400, SourceRequestError),
        (500, SourceUnavailableError),
        (503, SourceUnavailableError),
    ],
)
async def test_http_errors_map_to_typed_errors(
    http_client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    status: int,
    error: type[Exception],
) -> None:
    respx_mock.get(RU_URL).mock(return_value=httpx.Response(status, text=SECRET_BODY))

    with pytest.raises(error) as raised:
        await make_source(http_client).search("Мамай")

    assert str(status) in str(raised.value)
    assert SECRET_BODY not in str(raised.value)


async def test_network_error_is_unavailable(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(RU_URL).mock(side_effect=httpx.ConnectTimeout("boom"))

    with pytest.raises(SourceUnavailableError) as raised:
        await make_source(http_client).search("Мамай")

    assert "ConnectTimeout" in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [f"<html>{SECRET_BODY}</html>", "{}", json.dumps({"query": {"search": "no"}}), "[]"],
    ids=["html", "no-query", "wrong-shape", "list"],
)
async def test_unexpected_body_is_an_invalid_response(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter, body: str
) -> None:
    respx_mock.get(RU_URL).mock(return_value=httpx.Response(200, text=body))

    with pytest.raises(SourceInvalidResponseError) as raised:
        await make_source(http_client).search("Мамай")

    assert SECRET_BODY not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_failure_on_a_later_article_is_raised(
    http_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "list" in request.url.params:
            return httpx.Response(200, json=fixture_json("wikipedia_ru_search.json"))
        return httpx.Response(500)

    respx_mock.get(RU_URL).mock(side_effect=handler)

    with pytest.raises(SourceUnavailableError):
        await make_source(http_client).search("Мамай")


async def test_article_without_url_is_dropped_with_a_warning(
    http_client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    page = {"pageid": 1, "title": "Без адреса", "extract": "Текст статьи", "fullurl": "not a url"}
    body: dict[str, object] = {"batchcomplete": True, "query": {"pages": [page]}}
    serve(
        respx_mock, RU_URL, fixture_json("wikipedia_ru_search.json"), {118877: body, 3119654: body}
    )

    assert await make_source(http_client).search("Мамай") == []
    assert "dropped an invalid article" in caplog.text
    assert "Без адреса" not in caplog.text


RU_TEXT = "Введение.\n\n\n== Раздел ==\nТекст раздела.\n\n\n== Примечания ==\nСноска 1999."


def test_clean_extract_cuts_at_the_first_service_section() -> None:
    assert (
        clean_extract(RU_TEXT, WikipediaLanguage.RU, MAX_CHARS)
        == "Введение.\n\n\n== Раздел ==\nТекст раздела."
    )


def test_clean_extract_service_section_names_are_case_insensitive() -> None:
    text = "Intro.\n\n\n== References ==\nx"

    assert clean_extract(text, WikipediaLanguage.EN, MAX_CHARS) == "Intro."
    assert (
        clean_extract(text.replace("References", "REFERENCES"), WikipediaLanguage.EN, 99)
        == "Intro."
    )


def test_clean_extract_service_sections_are_per_language() -> None:
    text = "Intro.\n\n\n== Литература ==\nx"

    assert clean_extract(text, WikipediaLanguage.EN, MAX_CHARS) == text


def test_clean_extract_removes_stress_marks() -> None:
    assert clean_extract("Кулико́вская би́тва", WikipediaLanguage.RU, MAX_CHARS) == (
        "Куликовская битва"
    )


def test_clean_extract_text_starting_with_a_service_section_is_empty() -> None:
    assert clean_extract("== Примечания ==\nx", WikipediaLanguage.RU, MAX_CHARS) == ""


def test_clean_extract_empty_text_is_empty() -> None:
    assert clean_extract("", WikipediaLanguage.RU, MAX_CHARS) == ""


def test_clean_extract_cuts_at_a_line_boundary() -> None:
    text = "Первая строка.\nВторая строка.\nТретья строка."

    assert clean_extract(text, WikipediaLanguage.RU, 20) == "Первая строка."
    assert clean_extract(text, WikipediaLanguage.RU, 14) == "Первая строка."
    assert clean_extract(text, WikipediaLanguage.RU, len(text)) == text


def test_clean_extract_hard_cuts_a_single_long_line() -> None:
    assert clean_extract("a" * 50, WikipediaLanguage.RU, 10) == "a" * 10


def test_clean_extract_drops_a_heading_left_at_the_end() -> None:
    text = "Введение.\n\n\n== Раздел ==\nДлинный текст раздела без конца"

    assert clean_extract(text, WikipediaLanguage.RU, 24) == "Введение."
