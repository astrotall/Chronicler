import asyncio
import logging
from collections.abc import Mapping, Sequence

import pytest
from app.domain.research import SourceFailure
from app.domain.snippet import Snippet
from app.research.errors import SourceAuthError, SourceError, SourceUnavailableError
from app.services.research import ResearchLimits, ResearchService

from research_helpers import make_settings, make_snippet

type Reply = Sequence[Snippet] | SourceError | Exception

LIMITS = ResearchLimits(max_concurrency=5, snippet_max_chars=1000)


class FakeSource:
    def __init__(self, name: str, replies: Mapping[str, Reply] | None = None) -> None:
        self.name = name
        self._replies = replies or {}
        self.queries: list[str] = []

    async def search(self, query: str) -> list[Snippet]:
        self.queries.append(query)
        reply = self._replies.get(query, [])
        if isinstance(reply, Exception):
            raise reply
        return list(reply)


class GaugeSource:
    def __init__(self, name: str, gauge: "Gauge") -> None:
        self.name = name
        self._gauge = gauge

    async def search(self, query: str) -> list[Snippet]:
        self._gauge.running += 1
        self._gauge.peak = max(self._gauge.peak, self._gauge.running)
        await asyncio.sleep(0.01)
        self._gauge.running -= 1
        return [make_snippet(f"https://example.org/{self.name}/{query}")]


class Gauge:
    def __init__(self) -> None:
        self.running = 0
        self.peak = 0


def limits(**overrides: object) -> ResearchLimits:
    return LIMITS.model_copy(update=overrides)


async def test_snippets_from_all_sources_and_queries_are_collected() -> None:
    first = FakeSource("a", {"q1": [make_snippet("https://a.example/1")]})
    second = FakeSource("b", {"q2": [make_snippet("https://b.example/2")]})

    result = await ResearchService([first, second], LIMITS).research(["q1", "q2"])

    assert [snippet.url for snippet in result.snippets] == [
        "https://a.example/1",
        "https://b.example/2",
    ]
    assert result.failures == []
    assert first.queries == ["q1", "q2"]
    assert second.queries == ["q1", "q2"]


async def test_one_failing_query_does_not_drop_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    source = FakeSource(
        "a",
        {
            "q1": [make_snippet("https://a.example/1")],
            "q2": SourceUnavailableError("a", "HTTP 503"),
            "q3": [make_snippet("https://a.example/3")],
        },
    )

    result = await ResearchService([source], LIMITS).research(["q1", "q2", "q3"])

    assert [snippet.url for snippet in result.snippets] == [
        "https://a.example/1",
        "https://a.example/3",
    ]
    assert result.failures == [
        SourceFailure(source="a", query="q2", kind="SourceUnavailableError", reason="HTTP 503")
    ]
    assert "source=a kind=SourceUnavailableError reason=HTTP 503" in caplog.text


async def test_one_failing_source_does_not_drop_the_others() -> None:
    broken = FakeSource("broken", {"q": SourceAuthError("broken", "HTTP 401")})
    healthy = FakeSource("healthy", {"q": [make_snippet("https://h.example/1")]})

    result = await ResearchService([broken, healthy], LIMITS).research(["q"])

    assert [snippet.url for snippet in result.snippets] == ["https://h.example/1"]
    assert [(failure.source, failure.kind) for failure in result.failures] == [
        ("broken", "SourceAuthError")
    ]


async def test_all_sources_failing_is_a_result_not_an_exception() -> None:
    sources = [
        FakeSource(name, {"q": SourceUnavailableError(name, "HTTP 500")}) for name in ("a", "b")
    ]

    result = await ResearchService(sources, LIMITS).research(["q"])

    assert result.snippets == []
    assert [failure.source for failure in result.failures] == ["a", "b"]


async def test_sources_answering_with_nothing_is_a_normal_empty_result() -> None:
    result = await ResearchService([FakeSource("a"), FakeSource("b")], LIMITS).research(["q"])

    assert result.snippets == []
    assert result.failures == []


async def test_no_sources_and_no_queries_give_an_empty_result() -> None:
    assert (await ResearchService([], LIMITS).research(["q"])).snippets == []
    assert (await ResearchService([FakeSource("a")], LIMITS).research([])).snippets == []


async def test_unexpected_errors_are_not_swallowed() -> None:
    source = FakeSource("a", {"q": RuntimeError("bug")})

    with pytest.raises(RuntimeError):
        await ResearchService([source], LIMITS).research(["q"])


async def test_failure_records_do_not_contain_response_text() -> None:
    secret = "secret-response-text"
    source = FakeSource("a", {"q": make_failure_with_cause(secret)})

    result = await ResearchService([source], LIMITS).research(["q"])

    assert secret not in result.model_dump_json()


def make_failure_with_cause(secret: str) -> SourceError:
    error = SourceUnavailableError("a", "network error: ReadTimeout")
    error.__cause__ = ValueError(secret)
    return error


async def test_urls_are_deduplicated_after_normalisation() -> None:
    source = FakeSource(
        "a",
        {
            "q1": [make_snippet("https://Example.org/page/")],
            "q2": [
                make_snippet("https://example.org/page#section"),
                make_snippet("https://example.org/page"),
                make_snippet("https://example.org/other"),
            ],
        },
    )

    result = await ResearchService([source], LIMITS).research(["q1", "q2"])

    assert [snippet.url for snippet in result.snippets] == [
        "https://Example.org/page/",
        "https://example.org/other",
    ]


async def test_duplicate_url_keeps_the_longest_text() -> None:
    source = FakeSource(
        "a",
        {
            "q1": [make_snippet("https://example.org/page", "short")],
            "q2": [make_snippet("https://example.org/page#x", "a much longer chunk")],
            "q3": [make_snippet("https://example.org/page/", "mid size")],
        },
    )

    result = await ResearchService([source], LIMITS).research(["q1", "q2", "q3"])

    assert [snippet.text for snippet in result.snippets] == ["a much longer chunk"]
    assert result.snippets[0].url == "https://example.org/page#x"


async def test_duplicate_url_with_equal_length_keeps_the_first() -> None:
    source = FakeSource(
        "a",
        {
            "q1": [make_snippet("https://example.org/page", "first")],
            "q2": [make_snippet("https://example.org/page/", "other")],
        },
    )

    result = await ResearchService([source], LIMITS).research(["q1", "q2"])

    assert [snippet.text for snippet in result.snippets] == ["first"]


async def test_dedup_keeps_the_position_of_the_first_occurrence() -> None:
    source = FakeSource(
        "a",
        {
            "q1": [
                make_snippet("https://example.org/one", "x"),
                make_snippet("https://example.org/two", "y"),
            ],
            "q2": [make_snippet("https://example.org/one", "xxx")],
        },
    )

    result = await ResearchService([source], LIMITS).research(["q1", "q2"])

    assert [(snippet.url, snippet.text) for snippet in result.snippets] == [
        ("https://example.org/one", "xxx"),
        ("https://example.org/two", "y"),
    ]


async def test_same_page_from_two_sources_is_one_snippet() -> None:
    first = FakeSource("a", {"q": [make_snippet("https://example.org/page", "from a")]})
    second = FakeSource("b", {"q": [make_snippet("https://example.org/page", "from b longer")]})

    result = await ResearchService([first, second], LIMITS).research(["q"])

    assert [snippet.text for snippet in result.snippets] == ["from b longer"]


async def test_text_is_truncated_to_the_limit() -> None:
    long_snippet = make_snippet("https://example.org/long", "слово " * 10)
    short_snippet = make_snippet("https://example.org/short", "коротко")
    source = FakeSource("a", {"q": [long_snippet, short_snippet]})

    result = await ResearchService([source], limits(snippet_max_chars=12)).research(["q"])

    assert result.snippets[0].text == "слово слово"
    assert result.snippets[1].text == "коротко"
    assert result.snippets[0].id == long_snippet.id
    assert result.snippets[0].url == long_snippet.url


async def test_text_exactly_at_the_limit_is_untouched() -> None:
    source = FakeSource("a", {"q": [make_snippet("https://example.org/a", "x" * 10)]})

    result = await ResearchService([source], limits(snippet_max_chars=10)).research(["q"])

    assert result.snippets[0].text == "x" * 10


def domain_snippets() -> FakeSource:
    urls = [
        "https://example.org/a",
        "https://www.example.org/b",
        "https://notexample.org/c",
        "https://other.net/d",
        "https://Sub.Other.NET/e",
    ]
    return FakeSource("a", {"q": [make_snippet(url) for url in urls]})


async def urls_with(**overrides: object) -> list[str]:
    result = await ResearchService([domain_snippets()], limits(**overrides)).research(["q"])
    return [snippet.url for snippet in result.snippets]


async def test_domain_filters_are_empty_by_default() -> None:
    assert len(await urls_with()) == 5


async def test_blocked_domains_drop_the_domain_and_its_subdomains() -> None:
    assert await urls_with(blocked_domains=("example.org",)) == [
        "https://notexample.org/c",
        "https://other.net/d",
        "https://Sub.Other.NET/e",
    ]


async def test_allowed_domains_keep_only_the_domain_and_its_subdomains() -> None:
    assert await urls_with(allowed_domains=("other.net",)) == [
        "https://other.net/d",
        "https://Sub.Other.NET/e",
    ]


async def test_blocked_domains_win_over_allowed_ones() -> None:
    assert await urls_with(
        allowed_domains=("example.org", "other.net"), blocked_domains=("www.example.org",)
    ) == [
        "https://example.org/a",
        "https://other.net/d",
        "https://Sub.Other.NET/e",
    ]


async def test_allowed_domains_that_match_nothing_leave_nothing() -> None:
    assert await urls_with(allowed_domains=("nowhere.io",)) == []


async def test_domain_filter_runs_before_deduplication() -> None:
    source = FakeSource(
        "a",
        {
            "q": [
                make_snippet("https://bad.example/page", "very long blocked text"),
                make_snippet("https://good.example/page", "ok"),
            ]
        },
    )

    result = await ResearchService([source], limits(blocked_domains=("bad.example",))).research(
        ["q"]
    )

    assert [snippet.url for snippet in result.snippets] == ["https://good.example/page"]


async def test_concurrency_is_limited_by_the_semaphore() -> None:
    gauge = Gauge()
    sources = [GaugeSource(name, gauge) for name in ("a", "b", "c")]

    result = await ResearchService(sources, limits(max_concurrency=3)).research(
        ["q1", "q2", "q3", "q4"]
    )

    assert gauge.peak == 3
    assert len(result.snippets) == 12


async def test_concurrency_of_one_runs_searches_one_at_a_time() -> None:
    gauge = Gauge()
    sources = [GaugeSource(name, gauge) for name in ("a", "b")]

    await ResearchService(sources, limits(max_concurrency=1)).research(["q1", "q2"])

    assert gauge.peak == 1


def test_limits_are_built_from_settings() -> None:
    settings = make_settings(
        research_allowed_domains=["a.example"], research_blocked_domains=["b.example"]
    )

    built = ResearchLimits.from_settings(settings)

    assert built.max_concurrency == 5
    assert built.snippet_max_chars == 8000
    assert built.allowed_domains == ("a.example",)
    assert built.blocked_domains == ("b.example",)
