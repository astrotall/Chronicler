import pytest
from app.config.constants import ENV_FILE, ENV_FILE_ENCODING
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveKeys(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding=ENV_FILE_ENCODING, extra="ignore"
    )

    deepseek_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None


def has_live_key(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())


def skip_without_key(name: str, key: SecretStr | None) -> pytest.MarkDecorator:
    return pytest.mark.skipif(not has_live_key(key), reason=f"{name} is not set")
