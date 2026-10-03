from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config.constants import (
    ANTHROPIC_DEFAULT_API_VERSION,
    ANTHROPIC_DEFAULT_BASE_URL,
    ANTHROPIC_DEFAULT_MODEL,
    DEEPSEEK_DEFAULT_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    ENV_FILE,
    ENV_FILE_ENCODING,
    OWNER_IDS_SEPARATOR,
    LLMProvider,
    LLMStep,
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

    @field_validator("owner_telegram_ids", mode="before")
    @classmethod
    def split_owner_ids(cls, value: object) -> object:
        if isinstance(value, str):
            parts = (part.strip() for part in value.split(OWNER_IDS_SEPARATOR))
            return [part for part in parts if part]
        return value

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
