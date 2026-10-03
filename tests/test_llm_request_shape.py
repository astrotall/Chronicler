import json
import os
from collections.abc import Callable

import httpx
import pytest
import respx
from app.config.constants import LLMStep
from app.domain.llm import Message, Role
from app.llm.factory import LLMClientFactory
from pydantic import BaseModel

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 300

DEEPSEEK_OK: dict[str, object] = {
    "choices": [{"message": {"role": "assistant", "content": '{"name": "x"}'}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}
ANTHROPIC_OK: dict[str, object] = {
    "content": [{"type": "text", "text": '{"name": "x"}'}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Named(BaseModel):
    name: str


def conversation() -> list[Message]:
    return [
        Message(role=Role.SYSTEM, content="first system"),
        Message(role=Role.SYSTEM, content="second system"),
        Message(role=Role.USER, content="question"),
        Message(role=Role.ASSISTANT, content="answer"),
        Message(role=Role.USER, content="follow-up"),
    ]


def sent_body(route: respx.Route) -> dict[str, object]:
    body: dict[str, object] = json.loads(route.calls.last.request.content)
    return body


async def test_deepseek_text_request(
    make_factory: Callable[[], LLMClientFactory], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(DEEPSEEK_URL).mock(return_value=httpx.Response(200, json=DEEPSEEK_OK))
    client = make_factory().get_client(LLMStep.QUERY_PLANNING)

    await client.complete(conversation(), temperature=0.3, max_tokens=MAX_TOKENS)

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {os.environ['DEEPSEEK_API_KEY']}"
    assert request.headers["content-type"] == "application/json"
    assert sent_body(route) == {
        "model": "deepseek-flash",
        "messages": [message.model_dump(mode="json") for message in conversation()],
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
    }


async def test_deepseek_json_request_uses_json_mode_and_mentions_json(
    make_factory: Callable[[], LLMClientFactory], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(DEEPSEEK_URL).mock(return_value=httpx.Response(200, json=DEEPSEEK_OK))
    client = make_factory().get_client(LLMStep.FACT_EXTRACTION)

    await client.complete_json(conversation(), Named, max_tokens=MAX_TOKENS)

    body = sent_body(route)
    assert body["response_format"] == {"type": "json_object"}
    assert "temperature" not in body
    sent = body["messages"]
    assert isinstance(sent, list)
    assert [message["role"] for message in sent] == [
        "system",
        "system",
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "json" in sent[2]["content"]
    assert '"name"' in sent[2]["content"]


async def test_deepseek_thinking_mode_drops_temperature(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("DEEPSEEK_THINKING", "true")
    route = respx_mock.post(DEEPSEEK_URL).mock(return_value=httpx.Response(200, json=DEEPSEEK_OK))
    client = make_factory().get_client(LLMStep.WRITING)

    await client.complete(conversation(), temperature=0.3, max_tokens=MAX_TOKENS)

    body = sent_body(route)
    assert body["thinking"] == {"type": "enabled"}
    assert "temperature" not in body


async def test_deepseek_base_url_and_model_come_from_config(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://proxy.example.com/deepseek/")
    monkeypatch.setenv("LLM_WRITING_MODEL", "deepseek-v4-pro")
    route = respx_mock.post("https://proxy.example.com/deepseek/chat/completions").mock(
        return_value=httpx.Response(200, json=DEEPSEEK_OK)
    )
    client = make_factory().get_client(LLMStep.WRITING)

    await client.complete(conversation(), max_tokens=MAX_TOKENS)

    assert sent_body(route)["model"] == "deepseek-v4-pro"


async def test_anthropic_request(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_STYLE_CRITIQUE_PROVIDER", "anthropic")
    route = respx_mock.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=ANTHROPIC_OK))
    client = make_factory().get_client(LLMStep.STYLE_CRITIQUE)

    await client.complete(conversation(), temperature=0.3, max_tokens=MAX_TOKENS)

    request = route.calls.last.request
    assert request.headers["x-api-key"] == os.environ["ANTHROPIC_API_KEY"]
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers
    assert sent_body(route) == {
        "model": "claude-sonnet-5-5",
        "max_tokens": MAX_TOKENS,
        "system": "first system\n\nsecond system",
        "messages": [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
        ],
    }


async def test_anthropic_without_system_messages_omits_system(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")
    route = respx_mock.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=ANTHROPIC_OK))
    client = make_factory().get_client(LLMStep.WRITING)

    await client.complete([Message(role=Role.USER, content="question")], max_tokens=MAX_TOKENS)

    assert "system" not in sent_body(route)


async def test_anthropic_json_request_puts_the_schema_in_system(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")
    route = respx_mock.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=ANTHROPIC_OK))
    client = make_factory().get_client(LLMStep.WRITING)

    named = await client.complete_json(conversation(), Named, max_tokens=MAX_TOKENS)

    body = sent_body(route)
    assert named == Named(name="x")
    assert "response_format" not in body
    assert "output_config" not in body
    system = body["system"]
    assert isinstance(system, str)
    assert system.startswith("first system\n\nsecond system\n\n")
    assert '"name"' in system


async def test_anthropic_joins_several_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", "anthropic")
    reply = {
        "content": [
            {"type": "text", "text": "Первая часть. "},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": "Вторая часть."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    respx_mock.post(ANTHROPIC_URL).mock(return_value=httpx.Response(200, json=reply))
    client = make_factory().get_client(LLMStep.WRITING)

    result = await client.complete(conversation(), max_tokens=MAX_TOKENS)

    assert result.text == "Первая часть. Вторая часть."
