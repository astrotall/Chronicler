import logging

import pytest
from app.config.constants import LLMProvider, LLMStep, WikipediaLanguage
from app.config.settings import Settings
from app.domain.snippet import Snippet, SnippetOrigin
from app.llm.factory import LLMClientFactory, build_http_client
from app.research.wikipedia import ArticleResponse, clean_extract
from app.services.facts import FactLimits, extract_facts
from app.services.quote_check import QuoteCheck, check_quote

from live_keys import LiveKeys, skip_without_key
from research_helpers import fixture_json

KEYS = LiveKeys()
TOPIC = "Мамай, беклярбек Золотой Орды"
EXTRACT_MAX_CHARS = 6000
FIXTURES = (
    ("wikipedia_ru_article.json", WikipediaLanguage.RU, SnippetOrigin.WIKIPEDIA_RU),
    ("wikipedia_en_article.json", WikipediaLanguage.EN, SnippetOrigin.WIKIPEDIA_EN),
)

logger = logging.getLogger(__name__)


def fixture_snippet(name: str, language: WikipediaLanguage, origin: SnippetOrigin) -> Snippet:
    response = ArticleResponse.model_validate(fixture_json(name))
    assert response.query is not None
    [page] = response.query.pages
    assert page.extract is not None
    return Snippet(
        origin=origin,
        title=page.title,
        url=f"https://{language.value}.wikipedia.org/wiki/{page.title}",
        text=clean_extract(page.extract, language, EXTRACT_MAX_CHARS),
        lang=language.value,
    )


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_fact_extraction_on_wikipedia_fixtures() -> None:
    snippets = [fixture_snippet(*item) for item in FIXTURES]
    settings = Settings(
        telegram_bot_token="live-test",
        owner_telegram_ids=[1],
        deepseek_api_key=KEYS.deepseek_api_key,
        llm_query_planning_provider=LLMProvider.DEEPSEEK,
        llm_fact_extraction_provider=LLMProvider.DEEPSEEK,
        llm_writing_provider=LLMProvider.DEEPSEEK,
        llm_style_critique_provider=LLMProvider.DEEPSEEK,
    )
    async with build_http_client(settings) as http_client:
        client = LLMClientFactory(settings, http_client).get_client(LLMStep.FACT_EXTRACTION)

        result = await extract_facts(client, TOPIC, snippets, FactLimits.from_settings(settings))

    stats = result.stats
    logger.info("live fact extraction outcome=%s %s", result.outcome, stats.model_dump())
    assert stats.facts_verified > 0
    assert result.fact_set.facts
    by_id = {snippet.id: snippet for snippet in snippets}
    for fact in result.fact_set.facts:
        assert fact.support
        for ref in fact.support:
            assert ref.domain == "wikipedia.org"
            assert (
                check_quote(ref.quote, by_id[ref.snippet_id].text, settings.facts_min_quote_chars)
                is QuoteCheck.MATCH
            )
