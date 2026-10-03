import httpx
import pytest
from app.config.constants import TavilySearchDepth, WikipediaLanguage
from app.domain.snippet import SnippetOrigin
from app.research.tavily import TavilySource
from app.research.wikipedia import WikipediaSource

from live_keys import LiveKeys, skip_without_key

LIVE_CONTACT = "chronicler-live-test@example.invalid"
QUERY = "Куликовская битва"
KEYS = LiveKeys()
TAVILY_KEY = KEYS.tavily_api_key


@pytest.mark.integration
@pytest.mark.parametrize(
    ("language", "origin", "query"),
    [
        (WikipediaLanguage.RU, SnippetOrigin.WIKIPEDIA_RU, QUERY),
        (WikipediaLanguage.EN, SnippetOrigin.WIKIPEDIA_EN, "Battle of Kulikovo"),
    ],
)
async def test_live_wikipedia(
    language: WikipediaLanguage, origin: SnippetOrigin, query: str
) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        source = WikipediaSource(client, language, LIVE_CONTACT, 1, 3000)

        snippets = await source.search(query)

    assert snippets
    assert all(snippet.origin is origin for snippet in snippets)
    assert all(
        snippet.url.startswith(f"https://{language.value}.wikipedia.org/") for snippet in snippets
    )
    assert all(snippet.text and "́" not in snippet.text for snippet in snippets)
    assert all(len(snippet.text) <= 3000 for snippet in snippets)


@pytest.mark.integration
@skip_without_key("TAVILY_API_KEY", TAVILY_KEY)
async def test_live_tavily() -> None:
    assert TAVILY_KEY is not None
    async with httpx.AsyncClient(timeout=30) as client:
        source = TavilySource(client, TAVILY_KEY, TavilySearchDepth.BASIC, 2, 1)

        snippets = await source.search(QUERY)

    assert snippets
    assert all(snippet.origin is SnippetOrigin.TAVILY for snippet in snippets)
    assert all(snippet.url.startswith("http") and snippet.text for snippet in snippets)
