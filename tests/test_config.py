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


def facts_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> Settings:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OWNER_TELEGRAM_IDS", "42")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_facts_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(monkeypatch)

    assert settings.facts_input_max_chars == 60000
    assert settings.facts_min_quote_chars == 20
    assert settings.facts_max_facts == 20
    assert settings.facts_min_facts == 3
    assert settings.facts_domain_groups == [
        ["wikipedia.org", "wikimedia.org", "ruwiki.ru", "wikiwand.com"]
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a.org, B.org ; c.org;;", [["a.org", "b.org"], ["c.org"]]),
        ("wikipedia.org", [["wikipedia.org"]]),
        ("", []),
        (" ; , ", []),
    ],
)
def test_facts_domain_groups_are_parsed(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[list[str]]
) -> None:
    assert facts_env(monkeypatch, FACTS_DOMAIN_GROUPS=raw).facts_domain_groups == expected


def test_facts_minimum_above_maximum_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="FACTS_MIN_FACTS"):
        facts_env(monkeypatch, FACTS_MIN_FACTS="5", FACTS_MAX_FACTS="4")


@pytest.mark.parametrize(
    "name",
    ["FACTS_INPUT_MAX_CHARS", "FACTS_MIN_QUOTE_CHARS", "FACTS_MAX_FACTS", "FACTS_MIN_FACTS"],
)
def test_facts_limits_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    with pytest.raises(ValidationError):
        facts_env(monkeypatch, **{name: "0"})
