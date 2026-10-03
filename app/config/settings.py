from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config.constants import (
    ANTHROPIC_DEFAULT_API_VERSION,
    ANTHROPIC_DEFAULT_BASE_URL,
    ANTHROPIC_DEFAULT_MODEL,
    DEEPSEEK_DEFAULT_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    ENV_FILE,
    ENV_FILE_ENCODING,
    EXAMPLES_DEFAULT_DIR,
    EXAMPLES_DEFAULT_MAX,
    FACTS_DEFAULT_DOMAIN_GROUPS,
    FACTS_DEFAULT_INPUT_MAX_CHARS,
    FACTS_DEFAULT_MAX_FACTS,
    FACTS_DEFAULT_MIN_FACTS,
    FACTS_DEFAULT_MIN_QUOTE_CHARS,
    FACTS_DOMAIN_GROUPS_SEPARATOR,
    FACTS_DOMAIN_MEMBERS_SEPARATOR,
    LONG_DEFAULT_MAX_CHARS,
    OWNER_IDS_SEPARATOR,
    RESEARCH_DEFAULT_CONNECT_TIMEOUT_SECONDS,
    RESEARCH_DEFAULT_MAX_CONCURRENCY,
    RESEARCH_DEFAULT_READ_TIMEOUT_SECONDS,
    RESEARCH_DEFAULT_SNIPPET_MAX_CHARS,
    RESEARCH_DOMAINS_SEPARATOR,
    SHORT_DEFAULT_LENGTH_RETRIES,
    SHORT_DEFAULT_MAX_CHARS,
    SHORT_DEFAULT_MAX_FACTS,
    SHORT_DEFAULT_SENTENCE_CHARS,
    STYLE_DEFAULT_CRITIC_ENABLED,
    STYLE_DEFAULT_CRITIC_MAX_FINDINGS,
    STYLE_DEFAULT_MAX_REGENERATIONS,
    TAVILY_DEFAULT_CHUNKS_PER_SOURCE,
    TAVILY_DEFAULT_MAX_RESULTS,
    TAVILY_MAX_CHUNKS_PER_SOURCE,
    TAVILY_MAX_RESULTS_LIMIT,
    THREAD_DEFAULT_MAX_TWEETS,
    THREAD_MIN_TWEETS,
    THREAD_NUMBERING_TEMPLATE,
    THREAD_TWEET_DEFAULT_MAX_CHARS,
    WIKIPEDIA_DEFAULT_EXTRACT_MAX_CHARS,
    WIKIPEDIA_DEFAULT_MAX_ARTICLES,
    LLMProvider,
    LLMStep,
    TavilySearchDepth,
)

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class StepRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: LLMProvider
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding=ENV_FILE_ENCODING,
        extra="ignore",
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    owner_telegram_ids: Annotated[list[int], NoDecode] = Field(min_length=1)
    log_level: LogLevel = "INFO"

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = DEEPSEEK_DEFAULT_BASE_URL
    deepseek_model: str = Field(default=DEEPSEEK_DEFAULT_MODEL, min_length=1)
    deepseek_thinking: bool = False

    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = ANTHROPIC_DEFAULT_BASE_URL
    anthropic_model: str = Field(default=ANTHROPIC_DEFAULT_MODEL, min_length=1)
    anthropic_api_version: str = Field(default=ANTHROPIC_DEFAULT_API_VERSION, min_length=1)

    llm_query_planning_provider: LLMProvider = LLMProvider.DEEPSEEK
    llm_query_planning_model: str | None = None
    llm_fact_extraction_provider: LLMProvider = LLMProvider.DEEPSEEK
    llm_fact_extraction_model: str | None = None
    llm_writing_provider: LLMProvider = LLMProvider.DEEPSEEK
    llm_writing_model: str | None = None
    llm_style_critique_provider: LLMProvider = LLMProvider.DEEPSEEK
    llm_style_critique_model: str | None = None

    llm_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    llm_read_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_attempt_timeout_seconds: float = Field(default=300.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0)
    llm_retry_base_delay_seconds: float = Field(default=1.0, ge=0)
    llm_retry_max_delay_seconds: float = Field(default=30.0, ge=0)
    llm_json_max_retries: int = Field(default=2, ge=0)

    wikipedia_contact: str | None = None
    wikipedia_max_articles: int = Field(default=WIKIPEDIA_DEFAULT_MAX_ARTICLES, ge=1)
    wikipedia_extract_max_chars: int = Field(default=WIKIPEDIA_DEFAULT_EXTRACT_MAX_CHARS, ge=1)

    tavily_api_key: SecretStr | None = None
    tavily_search_depth: TavilySearchDepth = TavilySearchDepth.BASIC
    tavily_max_results: int = Field(
        default=TAVILY_DEFAULT_MAX_RESULTS, ge=1, le=TAVILY_MAX_RESULTS_LIMIT
    )
    tavily_chunks_per_source: int = Field(
        default=TAVILY_DEFAULT_CHUNKS_PER_SOURCE, ge=1, le=TAVILY_MAX_CHUNKS_PER_SOURCE
    )

    research_connect_timeout_seconds: float = Field(
        default=RESEARCH_DEFAULT_CONNECT_TIMEOUT_SECONDS, gt=0
    )
    research_read_timeout_seconds: float = Field(
        default=RESEARCH_DEFAULT_READ_TIMEOUT_SECONDS, gt=0
    )
    research_max_concurrency: int = Field(default=RESEARCH_DEFAULT_MAX_CONCURRENCY, ge=1)
    research_snippet_max_chars: int = Field(default=RESEARCH_DEFAULT_SNIPPET_MAX_CHARS, ge=1)
    research_allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    research_blocked_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

    facts_input_max_chars: int = Field(default=FACTS_DEFAULT_INPUT_MAX_CHARS, ge=1)
    facts_min_quote_chars: int = Field(default=FACTS_DEFAULT_MIN_QUOTE_CHARS, ge=1)
    facts_max_facts: int = Field(default=FACTS_DEFAULT_MAX_FACTS, ge=1)
    facts_min_facts: int = Field(default=FACTS_DEFAULT_MIN_FACTS, ge=1)
    facts_domain_groups: Annotated[list[list[str]], NoDecode] = Field(
        default_factory=lambda: [list(group) for group in FACTS_DEFAULT_DOMAIN_GROUPS]
    )

    short_max_chars: int = Field(default=SHORT_DEFAULT_MAX_CHARS, ge=1)
    short_max_facts: int = Field(default=SHORT_DEFAULT_MAX_FACTS, ge=1)
    short_sentence_chars: int = Field(default=SHORT_DEFAULT_SENTENCE_CHARS, ge=1)
    short_length_retries: int = Field(default=SHORT_DEFAULT_LENGTH_RETRIES, ge=0)
    short_drop_tail: bool = False
    long_max_chars: int = Field(default=LONG_DEFAULT_MAX_CHARS, ge=1)
    thread_tweet_max_chars: int = Field(default=THREAD_TWEET_DEFAULT_MAX_CHARS, ge=1)
    thread_max_tweets: int = Field(default=THREAD_DEFAULT_MAX_TWEETS, ge=THREAD_MIN_TWEETS)
    thread_numbering: bool = False

    examples_dir: Path = Path(EXAMPLES_DEFAULT_DIR)
    examples_max: int = Field(default=EXAMPLES_DEFAULT_MAX, ge=0)

    style_critic_enabled: bool = STYLE_DEFAULT_CRITIC_ENABLED
    style_max_regenerations: int = Field(default=STYLE_DEFAULT_MAX_REGENERATIONS, ge=0)
    style_critic_max_findings: int = Field(default=STYLE_DEFAULT_CRITIC_MAX_FINDINGS, ge=1)

    @field_validator("owner_telegram_ids", mode="before")
    @classmethod
    def split_owner_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = (part.strip() for part in value.split(OWNER_IDS_SEPARATOR))
            return [part for part in parts if part]
        return value

    @field_validator("research_allowed_domains", "research_blocked_domains", mode="before")
    @classmethod
    def split_domains(cls, value: object) -> object:
        if isinstance(value, str):
            parts = (part.strip().lower() for part in value.split(RESEARCH_DOMAINS_SEPARATOR))
            return [part for part in parts if part]
        return value

    @field_validator("facts_domain_groups", mode="before")
    @classmethod
    def split_domain_groups(cls, value: object) -> object:
        if isinstance(value, str):
            groups = (
                [
                    member.strip().lower()
                    for member in group.split(FACTS_DOMAIN_MEMBERS_SEPARATOR)
                    if member.strip()
                ]
                for group in value.split(FACTS_DOMAIN_GROUPS_SEPARATOR)
            )
            return [group for group in groups if group]
        return value

    @model_validator(mode="after")
    def require_facts_range(self) -> Self:
        if self.facts_min_facts > self.facts_max_facts:
            raise ValueError("FACTS_MIN_FACTS must not exceed FACTS_MAX_FACTS")
        return self

    @model_validator(mode="after")
    def require_room_for_numbering(self) -> Self:
        prefix = THREAD_NUMBERING_TEMPLATE.format(index=self.thread_max_tweets)
        if self.thread_numbering and len(prefix) >= self.thread_tweet_max_chars:
            raise ValueError("THREAD_TWEET_MAX_CHARS must leave room for the tweet numbering")
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator(
        "llm_query_planning_provider",
        "llm_fact_extraction_provider",
        "llm_writing_provider",
        "llm_style_critique_provider",
        "tavily_search_depth",
        mode="before",
    )
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "deepseek_api_key",
        "anthropic_api_key",
        "tavily_api_key",
        "wikipedia_contact",
        "llm_query_planning_model",
        "llm_fact_extraction_model",
        "llm_writing_model",
        "llm_style_critique_model",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    def api_key(self, provider: LLMProvider) -> SecretStr | None:
        keys = {
            LLMProvider.DEEPSEEK: self.deepseek_api_key,
            LLMProvider.ANTHROPIC: self.anthropic_api_key,
        }
        return keys[provider]

    def default_model(self, provider: LLMProvider) -> str:
        models = {
            LLMProvider.DEEPSEEK: self.deepseek_model,
            LLMProvider.ANTHROPIC: self.anthropic_model,
        }
        return models[provider]

    def llm_route(self, step: LLMStep) -> StepRoute:
        choices = {
            LLMStep.QUERY_PLANNING: (
                self.llm_query_planning_provider,
                self.llm_query_planning_model,
            ),
            LLMStep.FACT_EXTRACTION: (
                self.llm_fact_extraction_provider,
                self.llm_fact_extraction_model,
            ),
            LLMStep.WRITING: (self.llm_writing_provider, self.llm_writing_model),
            LLMStep.STYLE_CRITIQUE: (
                self.llm_style_critique_provider,
                self.llm_style_critique_model,
            ),
        }
        provider, model = choices[step]
        return StepRoute(provider=provider, model=model or self.default_model(provider))
