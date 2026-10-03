import json
from pathlib import Path

from app.config.settings import Settings
from app.domain.snippet import Snippet, SnippetOrigin

FIXTURES = Path(__file__).parent / "fixtures"
CONTACT = "bot-owner@example.invalid"
TAVILY_TEST_KEY = "tvly-test-key-123"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(fixture_text(name))
    return loaded


def make_settings(
    *,
    wikipedia_contact: str | None = CONTACT,
    tavily_api_key: str | None = TAVILY_TEST_KEY,
    tavily_search_depth: str = "basic",
    research_allowed_domains: list[str] | None = None,
    research_blocked_domains: list[str] | None = None,
) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123456:test",
        owner_telegram_ids=[1],
        wikipedia_contact=wikipedia_contact,
        tavily_api_key=tavily_api_key,
        tavily_search_depth=tavily_search_depth,
        research_allowed_domains=research_allowed_domains or [],
        research_blocked_domains=research_blocked_domains or [],
    )


def make_snippet(
    url: str,
    text: str = "text",
    origin: SnippetOrigin = SnippetOrigin.TAVILY,
    title: str = "title",
) -> Snippet:
    return Snippet(origin=origin, title=title, url=url, text=text)
