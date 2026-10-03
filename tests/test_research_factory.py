import logging

import httpx
import pytest
from app.config.settings import Settings
from app.research.factory import build_sources, log_source_availability
from pydantic import ValidationError

from research_helpers import CONTACT, TAVILY_TEST_KEY, make_settings

BLANK_VALUES = [None, "", "   ", "\t\n"]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WIKIPEDIA_CONTACT",
        "TAVILY_API_KEY",
        "TAVILY_SEARCH_DEPTH",
        "RESEARCH_ALLOWED_DOMAINS",
        "RESEARCH_BLOCKED_DOMAINS",
    ):
        monkeypatch.delenv(name, raising=False)


def source_names(settings: Settings, client: httpx.AsyncClient) -> list[str]:
    return [source.name for source in build_sources(settings, client)]


def test_all_sources_are_built_when_configured(http_client: httpx.AsyncClient) -> None:
    assert source_names(make_settings(), http_client) == [
        "wikipedia_ru",
        "wikipedia_en",
        "tavily",
    ]


@pytest.mark.parametrize("key", BLANK_VALUES)
def test_blank_tavily_key_disables_only_tavily(
    http_client: httpx.AsyncClient, key: str | None
) -> None:
    settings = make_settings(tavily_api_key=key)

    assert settings.tavily_api_key is None
    assert source_names(settings, http_client) == ["wikipedia_ru", "wikipedia_en"]


@pytest.mark.parametrize("contact", BLANK_VALUES)
def test_blank_wikipedia_contact_disables_only_wikipedia(
    http_client: httpx.AsyncClient, contact: str | None
) -> None:
    settings = make_settings(wikipedia_contact=contact)

    assert settings.wikipedia_contact is None
    assert source_names(settings, http_client) == ["tavily"]


def test_no_sources_when_nothing_is_configured(http_client: httpx.AsyncClient) -> None:
    settings = make_settings(wikipedia_contact=None, tavily_api_key=None)

    assert build_sources(settings, http_client) == []


@pytest.mark.parametrize("key", BLANK_VALUES)
def test_missing_tavily_key_is_logged_without_the_value(
    caplog: pytest.LogCaptureFixture, key: str | None
) -> None:
    caplog.set_level(logging.WARNING)

    log_source_availability(make_settings(tavily_api_key=key))

    assert [record.getMessage() for record in caplog.records] == [
        "TAVILY_API_KEY is not set, the Tavily source is disabled"
    ]


def test_missing_wikipedia_contact_is_logged_without_the_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    log_source_availability(make_settings(wikipedia_contact="  "))

    assert [record.getMessage() for record in caplog.records] == [
        "WIKIPEDIA_CONTACT is not set, the Wikipedia sources are disabled"
    ]


def test_no_enabled_source_is_a_warning_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    log_source_availability(make_settings(wikipedia_contact=None, tavily_api_key=None))

    assert len(caplog.records) == 3
    assert "no research source is enabled" in caplog.records[-1].getMessage()


def test_configured_sources_log_nothing_and_never_the_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    log_source_availability(make_settings())

    assert caplog.records == []
    assert TAVILY_TEST_KEY not in caplog.text
    assert CONTACT not in caplog.text


def test_tavily_key_is_not_exposed_by_repr() -> None:
    assert TAVILY_TEST_KEY not in repr(make_settings())


def test_settings_defaults() -> None:
    settings = make_settings()

    assert settings.tavily_search_depth == "basic"
    assert settings.tavily_max_results == 5
    assert settings.tavily_chunks_per_source == 3
    assert settings.wikipedia_max_articles == 2
    assert settings.wikipedia_extract_max_chars == 6000
    assert settings.research_max_concurrency == 5
    assert settings.research_allowed_domains == []
    assert settings.research_blocked_domains == []


def test_settings_read_the_research_variables_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "1")
    monkeypatch.setenv("TAVILY_API_KEY", f"  {TAVILY_TEST_KEY}  ")
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", " Advanced ")
    monkeypatch.setenv("WIKIPEDIA_CONTACT", f" {CONTACT} ")
    monkeypatch.setenv("RESEARCH_ALLOWED_DOMAINS", " Example.org , ,sub.example.com,")
    monkeypatch.setenv("RESEARCH_BLOCKED_DOMAINS", "")

    settings = Settings(_env_file=None)

    assert settings.tavily_api_key is not None
    assert settings.tavily_api_key.get_secret_value() == TAVILY_TEST_KEY
    assert settings.tavily_search_depth == "advanced"
    assert settings.wikipedia_contact == CONTACT
    assert settings.research_allowed_domains == ["example.org", "sub.example.com"]
    assert settings.research_blocked_domains == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAVILY_SEARCH_DEPTH", "turbo"),
        ("TAVILY_MAX_RESULTS", "0"),
        ("TAVILY_MAX_RESULTS", "21"),
        ("TAVILY_CHUNKS_PER_SOURCE", "4"),
        ("RESEARCH_MAX_CONCURRENCY", "0"),
        ("RESEARCH_SNIPPET_MAX_CHARS", "0"),
        ("WIKIPEDIA_MAX_ARTICLES", "0"),
    ],
)
def test_invalid_research_settings_are_an_error(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:test")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "1")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
