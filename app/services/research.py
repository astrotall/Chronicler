import asyncio
import logging
from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict

from app.config.settings import Settings
from app.domain.research import ResearchResult, SourceFailure
from app.domain.snippet import Snippet, normalize_url, url_host
from app.research.errors import SourceError
from app.research.source import ResearchSource

logger = logging.getLogger(__name__)

type SearchOutcome = list[Snippet] | SourceFailure


class ResearchLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_concurrency: int
    snippet_max_chars: int
    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            max_concurrency=settings.research_max_concurrency,
            snippet_max_chars=settings.research_snippet_max_chars,
            allowed_domains=tuple(settings.research_allowed_domains),
            blocked_domains=tuple(settings.research_blocked_domains),
        )


def host_matches(host: str, domains: Sequence[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


class ResearchService:
    def __init__(self, sources: Sequence[ResearchSource], limits: ResearchLimits) -> None:
        self._sources = list(sources)
        self._limits = limits

    async def research(self, queries: Sequence[str]) -> ResearchResult:
        semaphore = asyncio.Semaphore(self._limits.max_concurrency)
        outcomes = await asyncio.gather(
            *(
                self._search(semaphore, source, query)
                for source in self._sources
                for query in queries
            )
        )
        found: list[Snippet] = []
        failures: list[SourceFailure] = []
        for outcome in outcomes:
            if isinstance(outcome, SourceFailure):
                failures.append(outcome)
            else:
                found.extend(outcome)
        allowed = [snippet for snippet in found if self._is_allowed(snippet)]
        snippets = [self._truncate(snippet) for snippet in self._deduplicate(allowed)]
        return ResearchResult(snippets=snippets, failures=failures)

    async def _search(
        self, semaphore: asyncio.Semaphore, source: ResearchSource, query: str
    ) -> SearchOutcome:
        async with semaphore:
            try:
                return await source.search(query)
            except SourceError as error:
                logger.warning(
                    "research source failed source=%s kind=%s reason=%s",
                    source.name,
                    type(error).__name__,
                    error.reason,
                )
                return SourceFailure(
                    source=source.name,
                    query=query,
                    kind=type(error).__name__,
                    reason=error.reason,
                )

    def _is_allowed(self, snippet: Snippet) -> bool:
        host = url_host(snippet.url)
        if host_matches(host, self._limits.blocked_domains):
            return False
        return not self._limits.allowed_domains or host_matches(host, self._limits.allowed_domains)

    def _deduplicate(self, snippets: Sequence[Snippet]) -> list[Snippet]:
        by_url: dict[str, Snippet] = {}
        for snippet in snippets:
            key = normalize_url(snippet.url)
            kept = by_url.get(key)
            if kept is None or len(snippet.text) > len(kept.text):
                by_url[key] = snippet
        return list(by_url.values())

    def _truncate(self, snippet: Snippet) -> Snippet:
        if len(snippet.text) <= self._limits.snippet_max_chars:
            return snippet
        return snippet.model_copy(
            update={"text": snippet.text[: self._limits.snippet_max_chars].rstrip()}
        )
