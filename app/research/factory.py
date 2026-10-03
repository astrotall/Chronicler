import logging

import httpx

from app.config.constants import (
    TAVILY_API_KEY_ENV_NAME,
    WIKIPEDIA_CONTACT_ENV_NAME,
    WikipediaLanguage,
)
from app.config.settings import Settings
from app.research.source import ResearchSource
from app.research.tavily import TavilySource
from app.research.wikipedia import WikipediaSource

logger = logging.getLogger(__name__)


def build_sources(settings: Settings, client: httpx.AsyncClient) -> list[ResearchSource]:
    sources: list[ResearchSource] = []
    if settings.wikipedia_contact is not None:
        sources.extend(
            WikipediaSource(
                client,
                language,
                settings.wikipedia_contact,
                settings.wikipedia_max_articles,
                settings.wikipedia_extract_max_chars,
            )
            for language in WikipediaLanguage
        )
    if settings.tavily_api_key is not None:
        sources.append(
            TavilySource(
                client,
                settings.tavily_api_key,
                settings.tavily_search_depth,
                settings.tavily_max_results,
                settings.tavily_chunks_per_source,
            )
        )
    return sources


def log_source_availability(settings: Settings) -> None:
    if settings.wikipedia_contact is None:
        logger.warning(
            "%s is not set, the Wikipedia sources are disabled", WIKIPEDIA_CONTACT_ENV_NAME
        )
    if settings.tavily_api_key is None:
        logger.warning("%s is not set, the Tavily source is disabled", TAVILY_API_KEY_ENV_NAME)
    if settings.wikipedia_contact is None and settings.tavily_api_key is None:
        logger.warning("no research source is enabled, research will find nothing")
