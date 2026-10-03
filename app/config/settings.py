from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config.constants import ENV_FILE, ENV_FILE_ENCODING, OWNER_IDS_SEPARATOR

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding=ENV_FILE_ENCODING,
        extra="ignore",
    )

    telegram_bot_token: SecretStr = Field(min_length=1)
    owner_telegram_ids: Annotated[list[int], NoDecode] = Field(min_length=1)
    log_level: LogLevel = "INFO"

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
