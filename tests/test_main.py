import pytest
from app.config.constants import CONFIG_ERROR_HEADER
from app.config.settings import Settings
from app.main import describe_config_error
from pydantic import ValidationError

SECRET_TOKEN = "123456:secret-token"


def test_config_error_names_the_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "abc")

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)

    message = describe_config_error(raised.value)

    assert message.startswith(CONFIG_ERROR_HEADER)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "OWNER_TELEGRAM_IDS" in message


def test_config_error_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "abc")

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)

    assert SECRET_TOKEN not in describe_config_error(raised.value)
