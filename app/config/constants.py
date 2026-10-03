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
