from collections.abc import Callable
from pathlib import Path

import pytest
from app.config.constants import LLMProvider, LLMStep
from app.config.settings import Settings, StepRoute
from app.llm.anthropic import AnthropicClient
from app.llm.deepseek import DeepSeekClient
from app.llm.errors import LLMConfigError
from app.llm.factory import LLMClientFactory, validate_provider_keys
from app.main import load_settings
from pydantic import ValidationError


def settings() -> Settings:
    return Settings(_env_file=None)


def test_every_step_defaults_to_deepseek(llm_env: None) -> None:
    current = settings()

    for step in LLMStep:
        assert current.llm_route(step) == StepRoute(
            provider=LLMProvider.DEEPSEEK, model="deepseek-flash"
        )


def test_provider_is_chosen_per_step(llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_STYLE_CRITIQUE_PROVIDER", " Anthropic ")
    monkeypatch.setenv("LLM_STYLE_CRITIQUE_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("LLM_FACT_EXTRACTION_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_QUERY_PLANNING_MODEL", "  ")

    current = settings()

    assert current.llm_route(LLMStep.QUERY_PLANNING) == StepRoute(
        provider=LLMProvider.DEEPSEEK, model="deepseek-flash"
    )
    assert current.llm_route(LLMStep.FACT_EXTRACTION) == StepRoute(
        provider=LLMProvider.DEEPSEEK, model="deepseek-v4-pro"
    )
    assert current.llm_route(LLMStep.WRITING) == StepRoute(
        provider=LLMProvider.ANTHROPIC, model="claude-sonnet-5-5"
    )
    assert current.llm_route(LLMStep.STYLE_CRITIQUE) == StepRoute(
        provider=LLMProvider.ANTHROPIC, model="claude-opus-5-5"
    )


def test_provider_default_model_comes_from_config(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")

    assert settings().llm_route(LLMStep.WRITING).model == "claude-haiku-4-5"


def test_unknown_provider_is_a_config_error(llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "openai")

    with pytest.raises(ValidationError) as raised:
        settings()

    assert [error["loc"] for error in raised.value.errors()] == [("llm_writing_provider",)]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_JSON_MAX_RETRIES", "-1"),
        ("LLM_ATTEMPT_TIMEOUT_SECONDS", "0"),
        ("LLM_READ_TIMEOUT_SECONDS", "0"),
        ("DEEPSEEK_MODEL", ""),
    ],
)
def test_invalid_llm_limits_are_config_errors(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        settings()


def test_selected_provider_without_key_fails(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")

    with pytest.raises(LLMConfigError) as raised:
        validate_provider_keys(settings())

    message = str(raised.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "query_planning, fact_extraction, writing, style_critique" in message
    assert "ANTHROPIC_API_KEY" not in message
    assert "sk-" not in message


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_key_counts_as_missing(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", blank)

    with pytest.raises(LLMConfigError):
        validate_provider_keys(settings())


def test_anthropic_without_key_is_fine_when_not_selected(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    validate_provider_keys(settings())


def test_anthropic_selected_without_key_fails(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")

    with pytest.raises(LLMConfigError) as raised:
        validate_provider_keys(settings())

    assert "ANTHROPIC_API_KEY" in str(raised.value)
    assert "writing" in str(raised.value)
    assert "query_planning" not in str(raised.value)


def test_all_on_anthropic_needs_no_deepseek_key(
    llm_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    for step in LLMStep:
        monkeypatch.setenv(f"LLM_{step.upper()}_PROVIDER", "anthropic")

    validate_provider_keys(settings())


def test_factory_refuses_to_start_without_a_key(
    llm_env: None,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")

    with pytest.raises(LLMConfigError):
        make_factory()


def test_factory_returns_the_configured_client(
    monkeypatch: pytest.MonkeyPatch, make_factory: Callable[[], LLMClientFactory]
) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")
    factory = make_factory()

    planning = factory.get_client(LLMStep.QUERY_PLANNING)
    writing = factory.get_client(LLMStep.WRITING)

    assert isinstance(planning, DeepSeekClient)
    assert planning.target.step == LLMStep.QUERY_PLANNING
    assert planning.target.model == "deepseek-flash"
    assert isinstance(writing, AnthropicClient)
    assert writing.target.provider == LLMProvider.ANTHROPIC
    assert writing.target.model == "claude-sonnet-5-5"


def test_startup_stops_with_the_missing_variable(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY")

    with pytest.raises(SystemExit) as raised:
        load_settings()

    assert "DEEPSEEK_API_KEY" in str(raised.value)


def test_startup_reads_switches_from_the_env_file(
    llm_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    (tmp_path / ".env").write_text("LLM_WRITING_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-x\n")

    current = load_settings()

    assert current.llm_route(LLMStep.WRITING).provider == LLMProvider.ANTHROPIC
