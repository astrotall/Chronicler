import logging
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.constants import (
    HTTP_GET,
    USER_AGENT_HEADER,
    WIKIPEDIA_ACTION_QUERY,
    WIKIPEDIA_API_URL_TEMPLATE,
    WIKIPEDIA_ARTICLE_PROPS,
    WIKIPEDIA_CLIENT_NAME,
    WIKIPEDIA_CLIENT_VERSION,
    WIKIPEDIA_DISAMBIGUATION_PROP,
    WIKIPEDIA_EMPTY_PARAMETER,
    WIKIPEDIA_FLAG_ON,
    WIKIPEDIA_FORMAT_JSON,
    WIKIPEDIA_FORMAT_VERSION,
    WIKIPEDIA_HEADING_PATTERN,
    WIKIPEDIA_LIST_SEARCH,
    WIKIPEDIA_MAIN_NAMESPACE,
    WIKIPEDIA_SECTION_FORMAT,
    WIKIPEDIA_SERVICE_SECTIONS,
    WIKIPEDIA_STRESS_MARK,
    WIKIPEDIA_URL_INFO,
    WikipediaLanguage,
)
from app.domain.snippet import Snippet, SnippetOrigin
from app.research.errors import SourceInvalidResponseError, SourceRequestError
from app.research.http import parse_response, send_request

logger = logging.getLogger(__name__)

ORIGIN_BY_LANGUAGE: dict[WikipediaLanguage, SnippetOrigin] = {
    WikipediaLanguage.RU: SnippetOrigin.WIKIPEDIA_RU,
    WikipediaLanguage.EN: SnippetOrigin.WIKIPEDIA_EN,
}
HEADING_REGEX = re.compile(WIKIPEDIA_HEADING_PATTERN, re.MULTILINE)
LINE_BREAK = "\n"


class WikipediaSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    action: str = WIKIPEDIA_ACTION_QUERY
    response_format: str = Field(default=WIKIPEDIA_FORMAT_JSON, alias="format")
    formatversion: int = WIKIPEDIA_FORMAT_VERSION
    list_name: str = Field(default=WIKIPEDIA_LIST_SEARCH, alias="list")
    srsearch: str
    srlimit: int
    srnamespace: int = WIKIPEDIA_MAIN_NAMESPACE
    srprop: str = WIKIPEDIA_EMPTY_PARAMETER
    srinfo: str = WIKIPEDIA_EMPTY_PARAMETER


class WikipediaArticleRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    action: str = WIKIPEDIA_ACTION_QUERY
    response_format: str = Field(default=WIKIPEDIA_FORMAT_JSON, alias="format")
    formatversion: int = WIKIPEDIA_FORMAT_VERSION
    prop: str = WIKIPEDIA_ARTICLE_PROPS
    pageids: int
    explaintext: int = WIKIPEDIA_FLAG_ON
    exsectionformat: str = WIKIPEDIA_SECTION_FORMAT
    inprop: str = WIKIPEDIA_URL_INFO
    ppprop: str = WIKIPEDIA_DISAMBIGUATION_PROP


class WikipediaApiError(BaseModel):
    code: str = ""


class SearchHit(BaseModel):
    pageid: int
    title: str


class SearchQuery(BaseModel):
    search: list[SearchHit]


class SearchResponse(BaseModel):
    error: WikipediaApiError | None = None
    query: SearchQuery | None = None


class PageProps(BaseModel):
    disambiguation: str | None = None


class ArticlePage(BaseModel):
    pageid: int | None = None
    title: str = ""
    fullurl: str | None = None
    extract: str | None = None
    pageprops: PageProps | None = None


class ArticleQuery(BaseModel):
    pages: list[ArticlePage]


class ArticleResponse(BaseModel):
    error: WikipediaApiError | None = None
    query: ArticleQuery | None = None


def unwrap_query[T](source: str, error: WikipediaApiError | None, query: T | None) -> T:
    if error is not None:
        raise SourceRequestError(source, f"api error: {error.code}")
    if query is None:
        raise SourceInvalidResponseError(source, "unexpected response body")
    return query


def cut_at_service_section(text: str, service_sections: frozenset[str]) -> str:
    for match in HEADING_REGEX.finditer(text):
        if match.group(2).casefold() in service_sections:
            return text[: match.start()]
    return text


def drop_trailing_heading(text: str) -> str:
    lines = text.rstrip().split(LINE_BREAK)
    while lines and (not lines[-1].strip() or HEADING_REGEX.fullmatch(lines[-1].strip())):
        lines.pop()
    return LINE_BREAK.join(lines)


def cut_to_limit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    if text[max_chars] == LINE_BREAK:
        return head
    boundary = head.rfind(LINE_BREAK)
    return head[:boundary] if boundary > 0 else head


def clean_extract(text: str, language: WikipediaLanguage, max_chars: int) -> str:
    without_stress = text.replace(WIKIPEDIA_STRESS_MARK, "")
    body = cut_at_service_section(without_stress, WIKIPEDIA_SERVICE_SECTIONS[language]).strip()
    return drop_trailing_heading(cut_to_limit(body, max_chars))


class WikipediaSource:
    def __init__(
        self,
        client: httpx.AsyncClient,
        language: WikipediaLanguage,
        contact: str,
        max_articles: int,
        extract_max_chars: int,
    ) -> None:
        self.name = ORIGIN_BY_LANGUAGE[language].value
        self._client = client
        self._language = language
        self._url = WIKIPEDIA_API_URL_TEMPLATE.format(language=language.value)
        self._user_agent = (
            f"{WIKIPEDIA_CLIENT_NAME}/{WIKIPEDIA_CLIENT_VERSION} ({contact}) "
            f"httpx/{httpx.__version__}"
        )
        self._max_articles = max_articles
        self._extract_max_chars = extract_max_chars

    async def search(self, query: str) -> list[Snippet]:
        hits = await self._find_articles(query)
        snippets: list[Snippet] = []
        for hit in hits:
            snippet = await self._read_article(hit.pageid)
            if snippet is not None:
                snippets.append(snippet)
        return snippets

    async def _get(self, params: BaseModel) -> bytes:
        request = self._client.build_request(
            HTTP_GET,
            self._url,
            params=params.model_dump(by_alias=True),
            headers={USER_AGENT_HEADER: self._user_agent},
        )
        return await send_request(self.name, self._client, request)

    async def _find_articles(self, query: str) -> list[SearchHit]:
        content = await self._get(
            WikipediaSearchRequest(srsearch=query, srlimit=self._max_articles)
        )
        response = parse_response(self.name, SearchResponse, content)
        return unwrap_query(self.name, response.error, response.query).search

    async def _read_article(self, page_id: int) -> Snippet | None:
        content = await self._get(WikipediaArticleRequest(pageids=page_id))
        response = parse_response(self.name, ArticleResponse, content)
        for page in unwrap_query(self.name, response.error, response.query).pages:
            if page.pageprops is not None and page.pageprops.disambiguation is not None:
                continue
            if not page.fullurl or not page.extract:
                continue
            text = clean_extract(page.extract, self._language, self._extract_max_chars)
            try:
                return Snippet(
                    origin=ORIGIN_BY_LANGUAGE[self._language],
                    title=page.title,
                    url=page.fullurl,
                    text=text,
                    lang=self._language.value,
                )
            except ValidationError:
                logger.warning("research source=%s dropped an invalid article", self.name)
        return None
