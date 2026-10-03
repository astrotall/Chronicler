import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest
import respx
from app.config.constants import LLMProvider, LLMStep
from app.domain.llm import Message, Role
from app.llm.client import LLMClient
from app.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRequestError,
    LLMUnavailableError,
)
from app.llm.factory import LLMClientFactory
from app.prompts.json_reply import JSON_TRUNCATION_NOTE
from pydantic import BaseModel

type Body = dict[str, object]

PROMPT_TEXT = "Секретный текст промпта про 1453 год"
REPLY_TEXT = "Секретный текст ответа модели"
MAX_TOKENS = 256


@dataclass(frozen=True)
class ProviderCase:
    provider: LLMProvider
    url: str
    reply: Callable[[str | None, bool], Body]
    error: Callable[[str, str], Body]


def deepseek_reply(text: str | None, truncated: bool) -> Body:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "length" if truncated else "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def anthropic_reply(text: str | None, truncated: bool) -> Body:
    blocks: list[Body] = [{"type": "thinking", "thinking": "hidden", "signature": "sig"}]
    if text is not None:
        blocks.append({"type": "text", "text": text})
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": blocks,
        "stop_reason": "max_tokens" if truncated else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def deepseek_error(kind: str, message: str) -> Body:
    return {"error": {"message": message, "type": kind, "param": None, "code": kind}}


def anthropic_error(kind: str, message: str) -> Body:
    return {"type": "error", "error": {"type": kind, "message": message}, "request_id": "req_1"}


DEEPSEEK = ProviderCase(
    provider=LLMProvider.DEEPSEEK,
    url="https://api.deepseek.com/chat/completions",
    reply=deepseek_reply,
    error=deepseek_error,
)
ANTHROPIC = ProviderCase(
    provider=LLMProvider.ANTHROPIC,
    url="https://api.anthropic.com/v1/messages",
    reply=anthropic_reply,
    error=anthropic_error,
)
CASES = pytest.mark.parametrize("case", [DEEPSEEK, ANTHROPIC], ids=lambda case: case.provider)


class Capital(BaseModel):
    city: str
    founded: int


def messages() -> list[Message]:
    return [
        Message(role=Role.SYSTEM, content="You answer questions about history."),
        Message(role=Role.USER, content=PROMPT_TEXT),
    ]


def ok(case: ProviderCase, text: str | None, *, truncated: bool = False) -> httpx.Response:
    return httpx.Response(200, json=case.reply(text, truncated))


def client_for(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
) -> LLMClient:
    monkeypatch.setenv("LLM_WRITING_PROVIDER", case.provider)
    return make_factory().get_client(LLMStep.WRITING)


def sent_contents(route: respx.Route) -> list[str]:
    bodies = [json.loads(call.request.content) for call in route.calls]
    return [message["content"] for body in bodies for message in body["messages"]]


@CASES
async def test_complete_returns_text_and_usage(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(return_value=ok(case, REPLY_TEXT))
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.text == REPLY_TEXT
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.truncated is False


@CASES
async def test_complete_marks_a_truncated_reply(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(return_value=ok(case, REPLY_TEXT, truncated=True))
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.truncated is True


@CASES
@pytest.mark.parametrize("text", [None, "", "  \n"])
async def test_complete_rejects_an_empty_reply(
    case: ProviderCase,
    text: str | None,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(return_value=ok(case, text))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMInvalidResponseError):
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 1


@CASES
async def test_rate_limit_waits_for_retry_after(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "7"}),
            ok(case, REPLY_TEXT),
        ]
    )
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.text == REPLY_TEXT
    assert route.call_count == 2
    assert sleeps == [7.0]


@CASES
async def test_rate_limit_without_retry_after_backs_off_exponentially(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    respx_mock.post(case.url).mock(
        side_effect=[httpx.Response(429), httpx.Response(429), ok(case, REPLY_TEXT)]
    )
    client = client_for(case, monkeypatch, make_factory)

    await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert sleeps == [1.0, 2.0]


@CASES
async def test_retry_after_longer_than_the_cap_fails_at_once(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(
        return_value=httpx.Response(429, headers={"retry-after": "31"})
    )
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMRateLimitError):
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 1
    assert sleeps == []


@CASES
async def test_rate_limit_retries_are_exhausted(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(
        return_value=httpx.Response(429, json=case.error("rate_limit_error", "slow down"))
    )
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMRateLimitError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 4
    assert sleeps == [1.0, 2.0, 4.0]
    assert "429" in str(raised.value)
    assert "slow down" in str(raised.value)


@CASES
@pytest.mark.parametrize("status", [500, 503, 529])
async def test_server_error_is_retried(
    case: ProviderCase,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(
        side_effect=[httpx.Response(status), ok(case, REPLY_TEXT)]
    )
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.text == REPLY_TEXT
    assert route.call_count == 2
    assert sleeps == [1.0]


@CASES
async def test_server_error_retries_are_exhausted(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    route = respx_mock.post(case.url).mock(return_value=httpx.Response(503))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMUnavailableError):
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 2


@CASES
@pytest.mark.parametrize("status", [400, 402, 404, 413, 422])
async def test_client_error_is_not_retried(
    case: ProviderCase,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(
        return_value=httpx.Response(status, json=case.error("invalid_request_error", "bad"))
    )
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMRequestError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 1
    assert sleeps == []
    assert str(status) in str(raised.value)
    assert "writing" in str(raised.value)
    assert case.provider in str(raised.value)


@CASES
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error_is_not_retried(
    case: ProviderCase,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(
        return_value=httpx.Response(status, json=case.error("authentication_error", "bad key"))
    )
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMAuthError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert route.call_count == 1
    assert "sk-" not in str(raised.value)


@CASES
async def test_error_body_that_is_not_json_still_maps_to_a_typed_error(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(return_value=httpx.Response(400, text="<html>oops</html>"))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMRequestError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert raised.value.reason == "HTTP 400"


@CASES
@pytest.mark.parametrize(
    "network_error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("eof")],
)
async def test_network_error_is_retried(
    case: ProviderCase,
    network_error: httpx.HTTPError,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    route = respx_mock.post(case.url).mock(side_effect=[network_error, ok(case, REPLY_TEXT)])
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.text == REPLY_TEXT
    assert route.call_count == 2
    assert sleeps == [1.0]


@CASES
async def test_network_retries_are_exhausted_without_leaking_httpx(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(side_effect=httpx.ConnectError("refused"))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert type(raised.value) is LLMUnavailableError
    assert "ConnectError" in str(raised.value)


class SlowProvider:
    def __init__(self, replies: list[httpx.Response | None]) -> None:
        self.replies = replies
        self.started = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        reply = self.replies[min(self.started, len(self.replies) - 1)]
        self.started += 1
        if reply is None:
            await asyncio.sleep(5)
            return httpx.Response(200)
        return reply


@CASES
async def test_attempt_deadline_is_a_network_error(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    sleeps: list[float],
) -> None:
    monkeypatch.setenv("LLM_ATTEMPT_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    provider = SlowProvider([None])
    respx_mock.post(case.url).mock(side_effect=provider)
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMUnavailableError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert provider.started == 2
    assert sleeps == [1.0]
    assert "TimeoutError" in str(raised.value)


@CASES
async def test_attempt_deadline_then_success(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_ATTEMPT_TIMEOUT_SECONDS", "0.05")
    provider = SlowProvider([None, ok(case, REPLY_TEXT)])
    respx_mock.post(case.url).mock(side_effect=provider)
    client = client_for(case, monkeypatch, make_factory)

    result = await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert result.text == REPLY_TEXT
    assert provider.started == 2


@CASES
async def test_unexpected_success_body_is_an_invalid_response(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(
        return_value=httpx.Response(200, json={"unexpected": REPLY_TEXT})
    )
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMInvalidResponseError) as raised:
        await client.complete(messages(), max_tokens=MAX_TOKENS)

    assert REPLY_TEXT not in str(raised.value)
    assert raised.value.__cause__ is None


@CASES
@pytest.mark.parametrize(
    "conversation",
    [[], [Message(role=Role.SYSTEM, content="system only")]],
    ids=["empty", "system-only"],
)
async def test_request_without_a_user_message_is_rejected_locally(
    case: ProviderCase,
    conversation: list[Message],
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(return_value=ok(case, REPLY_TEXT))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMRequestError):
        await client.complete(conversation, max_tokens=MAX_TOKENS)

    assert route.call_count == 0


@CASES
async def test_complete_json_returns_the_model(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(case.url).mock(
        return_value=ok(case, '{"city": "Константинополь", "founded": 330}')
    )
    client = client_for(case, monkeypatch, make_factory)

    capital = await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert capital == Capital(city="Константинополь", founded=330)


@CASES
async def test_complete_json_reads_a_fenced_reply(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    fenced = 'Вот ответ:\n```json\n{"city": "Рим", "founded": -753}\n```\n'
    respx_mock.post(case.url).mock(return_value=ok(case, fenced))
    client = client_for(case, monkeypatch, make_factory)

    capital = await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert capital == Capital(city="Рим", founded=-753)


@CASES
@pytest.mark.parametrize(
    "first_reply",
    ['{"city": "Рим", "founded": ', '{"city": "Рим"}', '{"city": "Рим", "founded": "давно"}'],
    ids=["invalid-json", "missing-field", "wrong-type"],
)
async def test_complete_json_retries_with_the_validation_error(
    case: ProviderCase,
    first_reply: str,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(
        side_effect=[ok(case, first_reply), ok(case, '{"city": "Рим", "founded": -753}')]
    )
    client = client_for(case, monkeypatch, make_factory)

    capital = await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert capital.founded == -753
    assert route.call_count == 2
    second_request = sent_contents(route)[
        len(json.loads(route.calls[0].request.content)["messages"]) :
    ]
    assert first_reply in second_request
    assert any("not a valid json object" in content for content in second_request)
    assert all(JSON_TRUNCATION_NOTE not in content for content in second_request)


@CASES
async def test_truncated_invalid_json_asks_for_a_shorter_reply(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(
        side_effect=[
            ok(case, '{"city": "Рим", "foun', truncated=True),
            ok(case, '{"city": "Рим", "founded": -753}'),
        ]
    )
    client = client_for(case, monkeypatch, make_factory)

    await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    correction = json.loads(route.calls[1].request.content)["messages"][-1]["content"]
    assert JSON_TRUNCATION_NOTE in correction


@CASES
@pytest.mark.parametrize("empty", [None, "", "   "])
async def test_empty_json_reply_spends_an_attempt(
    case: ProviderCase,
    empty: str | None,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(case.url).mock(
        side_effect=[ok(case, empty), ok(case, '{"city": "Рим", "founded": -753}')]
    )
    client = client_for(case, monkeypatch, make_factory)

    capital = await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert capital.city == "Рим"
    assert route.call_count == 2
    assert route.calls[0].request.content == route.calls[1].request.content


@CASES
async def test_complete_json_gives_up_after_the_configured_attempts(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_JSON_MAX_RETRIES", "2")
    route = respx_mock.post(case.url).mock(return_value=ok(case, REPLY_TEXT))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMInvalidResponseError) as raised:
        await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert route.call_count == 3
    assert REPLY_TEXT not in str(raised.value)
    assert raised.value.__cause__ is None
    assert "Capital" in str(raised.value)


@CASES
async def test_complete_json_with_zero_retries_makes_one_attempt(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
) -> None:
    monkeypatch.setenv("LLM_JSON_MAX_RETRIES", "0")
    route = respx_mock.post(case.url).mock(return_value=ok(case, "not json"))
    client = client_for(case, monkeypatch, make_factory)

    with pytest.raises(LLMInvalidResponseError):
        await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    assert route.call_count == 1


@CASES
async def test_logs_carry_metrics_and_never_text(
    case: ProviderCase,
    monkeypatch: pytest.MonkeyPatch,
    make_factory: Callable[[], LLMClientFactory],
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.post(case.url).mock(
        side_effect=[httpx.Response(503), ok(case, REPLY_TEXT), ok(case, '{"city": "Рим"')]
        + [ok(case, REPLY_TEXT)] * 3
    )
    client = client_for(case, monkeypatch, make_factory)

    with caplog.at_level(logging.DEBUG):
        await client.complete(messages(), max_tokens=MAX_TOKENS)
        with pytest.raises(LLMInvalidResponseError):
            await client.complete_json(messages(), Capital, max_tokens=MAX_TOKENS)

    call_lines = [record.getMessage() for record in caplog.records if "llm call" in record.msg]
    assert f"provider={case.provider}" in call_lines[0]
    assert "step=writing" in call_lines[0]
    assert "input_tokens=11 output_tokens=7" in call_lines[0]
    assert "retries=1" in call_lines[0]
    assert "duration_ms=" in call_lines[0]
    assert PROMPT_TEXT not in caplog.text
    assert REPLY_TEXT not in caplog.text
    assert "Рим" not in caplog.text
    assert "sk-" not in caplog.text
