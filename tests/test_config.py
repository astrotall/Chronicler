from pathlib import Path

import pytest
from app.config.constants import FACTS_DEFAULT_WEAK_DOMAINS
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


def test_facts_weak_domain_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(monkeypatch)

    assert settings.facts_max_per_domain == 6
    assert tuple(settings.facts_weak_domains) == FACTS_DEFAULT_WEAK_DOMAINS
    assert len(settings.facts_weak_domains) == 18
    assert {"youtube.com", "otvet.mail.ru", "slider-ai.ru"} <= set(settings.facts_weak_domains)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" YouTube.com, vk.com ,,", ["youtube.com", "vk.com"]),
        ("vk.com", ["vk.com"]),
        ("", []),
        (" , ", []),
    ],
)
def test_facts_weak_domains_are_parsed(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    assert facts_env(monkeypatch, FACTS_WEAK_DOMAINS=raw).facts_weak_domains == expected


def test_facts_max_per_domain_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        facts_env(monkeypatch, FACTS_MAX_PER_DOMAIN="0")


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


def test_writing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(monkeypatch)

    assert settings.short_max_chars == 280
    assert settings.long_max_chars == 25000
    assert settings.long_min_chars == 1200
    assert settings.long_min_used_facts == 6
    assert settings.style_min_retained_chars_ratio == 0.6
    assert settings.style_min_retained_facts_ratio == 0.6
    assert settings.thread_tweet_max_chars == 280
    assert settings.thread_max_tweets == 12
    assert settings.thread_min_tweets == 4
    assert settings.thread_min_used_facts == 5
    assert settings.thread_numbering is False
    assert settings.examples_dir == Path("data/examples")
    assert settings.examples_max == 3


def test_writing_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(
        monkeypatch,
        SHORT_MAX_CHARS="200",
        THREAD_MAX_TWEETS="5",
        THREAD_NUMBERING="true",
        EXAMPLES_DIR="/tmp/posts",
        EXAMPLES_MAX="0",
    )

    assert settings.short_max_chars == 200
    assert settings.thread_max_tweets == 5
    assert settings.thread_numbering is True
    assert settings.examples_dir == Path("/tmp/posts")
    assert settings.examples_max == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SHORT_MAX_CHARS", "0"),
        ("LONG_MAX_CHARS", "0"),
        ("LONG_MIN_CHARS", "-1"),
        ("LONG_MIN_USED_FACTS", "-1"),
        ("THREAD_MIN_TWEETS", "-1"),
        ("THREAD_MIN_USED_FACTS", "-1"),
        ("STYLE_MIN_RETAINED_CHARS_RATIO", "0"),
        ("STYLE_MIN_RETAINED_CHARS_RATIO", "1.1"),
        ("STYLE_MIN_RETAINED_FACTS_RATIO", "0"),
        ("THREAD_TWEET_MAX_CHARS", "0"),
        ("THREAD_MAX_TWEETS", "1"),
        ("EXAMPLES_MAX", "-1"),
    ],
)
def test_writing_limits_out_of_range_are_an_error(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        facts_env(monkeypatch, **{name: value})


def test_numbering_must_fit_into_the_tweet_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="THREAD_TWEET_MAX_CHARS"):
        facts_env(monkeypatch, THREAD_NUMBERING="true", THREAD_TWEET_MAX_CHARS="4")

    monkeypatch.setenv("THREAD_NUMBERING", "false")
    assert facts_env(monkeypatch, THREAD_TWEET_MAX_CHARS="4").thread_tweet_max_chars == 4


def test_the_long_minimum_must_not_exceed_the_long_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="LONG_MIN_CHARS"):
        facts_env(monkeypatch, LONG_MIN_CHARS="3000", LONG_MAX_CHARS="2000")

    assert (
        facts_env(monkeypatch, LONG_MIN_CHARS="2000", LONG_MAX_CHARS="2000").long_min_chars == 2000
    )


def test_the_thread_tweet_minimum_must_not_exceed_the_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="THREAD_MIN_TWEETS"):
        facts_env(monkeypatch, THREAD_MIN_TWEETS="13", THREAD_MAX_TWEETS="12")

    assert (
        facts_env(monkeypatch, THREAD_MIN_TWEETS="12", THREAD_MAX_TWEETS="12").thread_min_tweets
        == 12
    )


def test_the_thread_fact_minimum_must_agree_with_the_thread_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="THREAD_MIN_USED_FACTS"):
        facts_env(monkeypatch, THREAD_MIN_FACTS="4")

    settings = facts_env(monkeypatch, THREAD_MIN_FACTS="4", THREAD_MIN_USED_FACTS="4")
    assert settings.thread_min_used_facts == 4


def test_the_thread_minimums_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(monkeypatch, THREAD_MIN_TWEETS="0", THREAD_MIN_USED_FACTS="0")

    assert (settings.thread_min_tweets, settings.thread_min_used_facts) == (0, 0)


def test_the_thread_selection_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = facts_env(monkeypatch)

    assert settings.thread_max_facts == 10
    assert settings.thread_max_attributed == 2


def test_the_thread_selection_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = facts_env(monkeypatch, THREAD_MAX_FACTS="8", THREAD_MAX_ATTRIBUTED="0")

    assert (settings.thread_max_facts, settings.thread_max_attributed) == (8, 0)
    assert facts_env(monkeypatch, THREAD_MAX_FACTS="0").thread_max_facts == 0


@pytest.mark.parametrize(
    ("name", "value"), [("THREAD_MAX_FACTS", "-1"), ("THREAD_MAX_ATTRIBUTED", "-1")]
)
def test_a_negative_thread_selection_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        facts_env(monkeypatch, **{name: value})


def test_the_thread_cap_must_not_be_below_the_thread_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        ValidationError, match="THREAD_MAX_FACTS must not be below THREAD_MIN_FACTS"
    ):
        facts_env(monkeypatch, THREAD_MAX_FACTS="4")

    assert facts_env(monkeypatch, THREAD_MAX_FACTS="5").thread_max_facts == 5
    assert facts_env(monkeypatch, THREAD_MAX_FACTS="0").thread_max_facts == 0
    settings = facts_env(
        monkeypatch, THREAD_MAX_FACTS="3", THREAD_MIN_FACTS="3", THREAD_MIN_USED_FACTS="3"
    )
    assert settings.thread_max_facts == 3
