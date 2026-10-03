from pathlib import Path

import pytest
from app.config.settings import Settings
from pydantic import ValidationError

TOKEN = "123456:secret-token"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "OWNER_TELEGRAM_IDS", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)


def test_loads_required_fields_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")

    settings = Settings(_env_file=None)

    assert settings.telegram_bot_token.get_secret_value() == TOKEN
    assert settings.owner_telegram_ids == [42]
    assert settings.log_level == "INFO"


def test_parses_comma_separated_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "1, 2 ,3,")

    settings = Settings(_env_file=None)

    assert settings.owner_telegram_ids == [1, 2, 3]


def test_missing_token_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)

    assert [error["loc"] for error in raised.value.errors()] == [("telegram_bot_token",)]


def test_missing_owner_ids_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)

    assert [error["loc"] for error in raised.value.errors()] == [("owner_telegram_ids",)]


@pytest.mark.parametrize("raw", ["", " , ", "abc", "1,abc"])
def test_invalid_owner_ids_are_an_error(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", raw)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_empty_token_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(("raw", "expected"), [("debug", "DEBUG"), (" Warning ", "WARNING")])
def test_log_level_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")
    monkeypatch.setenv("LOG_LEVEL", raw)

    assert Settings(_env_file=None).log_level == expected


def test_unknown_log_level_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nOWNER_TELEGRAM_IDS=7,8\n")

    settings = Settings(_env_file=env_file)

    assert settings.owner_telegram_ids == [7, 8]
