from enum import StrEnum

ENV_FILE = ".env"
ENV_FILE_ENCODING = "utf-8"
OWNER_IDS_SEPARATOR = ","
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
CONFIG_ERROR_HEADER = "Invalid configuration, check the environment and the .env file:"


class LLMStep(StrEnum):
    QUERY_PLANNING = "query_planning"
    FACT_EXTRACTION = "fact_extraction"
    WRITING = "writing"
    STYLE_CRITIQUE = "style_critique"


class LLMProvider(StrEnum):
    DEEPSEEK = "deepseek"
    ANTHROPIC = "anthropic"


API_KEY_ENV_NAMES: dict[LLMProvider, str] = {
    LLMProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
    LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
}

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"
DEEPSEEK_CHAT_PATH = "/chat/completions"
DEEPSEEK_AUTHORIZATION_HEADER = "Authorization"
DEEPSEEK_BEARER_PREFIX = "Bearer "
DEEPSEEK_JSON_RESPONSE_FORMAT = "json_object"
DEEPSEEK_THINKING_ENABLED = "enabled"
DEEPSEEK_THINKING_DISABLED = "disabled"
DEEPSEEK_TRUNCATED_FINISH_REASON = "length"

ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-5-5"
ANTHROPIC_DEFAULT_API_VERSION = "2023-06-01"
ANTHROPIC_MESSAGES_PATH = "/v1/messages"
ANTHROPIC_API_KEY_HEADER = "x-api-key"
ANTHROPIC_VERSION_HEADER = "anthropic-version"
ANTHROPIC_TEXT_BLOCK_TYPE = "text"
ANTHROPIC_TRUNCATED_STOP_REASON = "max_tokens"
ANTHROPIC_SYSTEM_SEPARATOR = "\n\n"

RETRY_AFTER_HEADER = "retry-after"
CONTENT_TYPE_HEADER = "content-type"
JSON_CONTENT_TYPE = "application/json"


class WikipediaLanguage(StrEnum):
    RU = "ru"
    EN = "en"


class TavilySearchDepth(StrEnum):
    BASIC = "basic"
    ADVANCED = "advanced"
    FAST = "fast"
    ULTRA_FAST = "ultra-fast"


QUERY_COUNT_MIN = 3
QUERY_COUNT_MAX = 5
QUERY_PLANNING_MAX_TOKENS = 2000

HTTP_GET = "GET"
HTTP_POST = "POST"
USER_AGENT_HEADER = "User-Agent"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "

RESEARCH_DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
RESEARCH_DEFAULT_READ_TIMEOUT_SECONDS = 30.0
RESEARCH_DEFAULT_MAX_CONCURRENCY = 5
RESEARCH_DEFAULT_SNIPPET_MAX_CHARS = 8000
RESEARCH_DOMAINS_SEPARATOR = ","

WIKIPEDIA_API_URL_TEMPLATE = "https://{language}.wikipedia.org/w/api.php"
WIKIPEDIA_CLIENT_NAME = "ChroniclerBot"
WIKIPEDIA_CLIENT_VERSION = "0.1"
WIKIPEDIA_DEFAULT_MAX_ARTICLES = 2
WIKIPEDIA_DEFAULT_EXTRACT_MAX_CHARS = 6000
WIKIPEDIA_ACTION_QUERY = "query"
WIKIPEDIA_FORMAT_JSON = "json"
WIKIPEDIA_FORMAT_VERSION = 2
WIKIPEDIA_LIST_SEARCH = "search"
WIKIPEDIA_MAIN_NAMESPACE = 0
WIKIPEDIA_EMPTY_PARAMETER = ""
WIKIPEDIA_FLAG_ON = 1
WIKIPEDIA_ARTICLE_PROPS = "extracts|info|pageprops"
WIKIPEDIA_DISAMBIGUATION_PROP = "disambiguation"
WIKIPEDIA_URL_INFO = "url"
WIKIPEDIA_SECTION_FORMAT = "wiki"
WIKIPEDIA_STRESS_MARK = "́"
WIKIPEDIA_HEADING_PATTERN = r"^(={2,6})[ \t]*(.+?)[ \t]*\1[ \t]*$"
WIKIPEDIA_SERVICE_SECTIONS: dict[WikipediaLanguage, frozenset[str]] = {
    WikipediaLanguage.RU: frozenset(
        {"примечания", "литература", "ссылки", "см. также", "источники", "комментарии"}
    ),
    WikipediaLanguage.EN: frozenset(
        {
            "references",
            "external links",
            "further reading",
            "see also",
            "notes",
            "bibliography",
            "sources",
            "footnotes",
            "citations",
        }
    ),
}

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_DEFAULT_MAX_RESULTS = 5
TAVILY_DEFAULT_CHUNKS_PER_SOURCE = 3
TAVILY_MAX_RESULTS_LIMIT = 20
TAVILY_MAX_CHUNKS_PER_SOURCE = 3
TAVILY_DEPTHS_WITHOUT_CHUNKS = frozenset({TavilySearchDepth.ULTRA_FAST})

WIKIPEDIA_CONTACT_ENV_NAME = "WIKIPEDIA_CONTACT"
TAVILY_API_KEY_ENV_NAME = "TAVILY_API_KEY"

FACTS_DEFAULT_INPUT_MAX_CHARS = 60000
FACTS_DEFAULT_MIN_QUOTE_CHARS = 20
FACTS_DEFAULT_MAX_FACTS = 20
FACTS_DEFAULT_MIN_FACTS = 3
FACTS_DEFAULT_DOMAIN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("wikipedia.org", "wikimedia.org", "ruwiki.ru", "wikiwand.com"),
)
FACTS_DOMAIN_GROUPS_SEPARATOR = ";"
FACTS_DOMAIN_MEMBERS_SEPARATOR = ","
FACT_EXTRACTION_MAX_TOKENS = 8000
FACT_CANDIDATES_MAX = 40
FACT_SUPPORT_MAX = 6
DISPUTE_CHECK_MAX_TOKENS = 3000
DISPUTE_EXPLANATION_MAX_CHARS = 500
CONFIRMED_MIN_DOMAINS = 2
DISPUTE_MIN_FACTS = 2
SNIPPET_ALIAS_PREFIX = "S"
CANDIDATE_ID_PREFIX = "C"
FACT_ID_PREFIX = "F"
WWW_PREFIX = "www."
DOMAIN_LABEL_SEPARATOR = "."
REGISTRABLE_DOMAIN_LABELS = 2
