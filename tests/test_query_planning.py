import json
from collections.abc import Callable

import httpx
import pytest
import respx
from app.config.constants import LLMStep
from app.domain.llm import Role
from app.llm.errors import LLMInvalidResponseError, LLMUnavailableError
from app.llm.factory import LLMClientFactory
from app.prompts.query_planning import render_query_planning
from app.services.query_planning import QueryPlan, plan_queries

from llm_helpers import ScriptedLLMClient, as_client

TOPIC = "Куликовская битва"
VALID = ["Куликовская битва 1380", "Battle of Kulikovo", "Мамай и Дмитрий Донской"]
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


async def test_valid_plan_returns_the_queries() -> None:
    fake = ScriptedLLMClient({"queries": VALID})

    assert await plan_queries(as_client(fake), TOPIC) == VALID


@pytest.mark.parametrize("count", [3, 4, 5])
async def test_three_to_five_queries_are_accepted(count: int) -> None:
    queries = [f"запрос {number}" for number in range(count)]

    assert await plan_queries(as_client(ScriptedLLMClient({"queries": queries})), TOPIC) == queries


async def test_queries_are_stripped() -> None:
    fake = ScriptedLLMClient({"queries": ["  один ", "два\n", "\tthree"]})

    assert await plan_queries(as_client(fake), TOPIC) == ["один", "два", "three"]


@pytest.mark.parametrize(
    "queries",
    [
        [],
        ["один", "два"],
        ["один", "два", "три", "четыре", "пять", "шесть"],
        ["один", "два", ""],
        ["один", "два", "   "],
        ["один", "два", "один"],
        ["Battle", "battle", "другой"],
        ["один", "два", " один "],
        ["один", "два", 3],
        ["один", "два", None],
    ],
    ids=[
        "empty",
        "too-few",
        "too-many",
        "empty-string",
        "blank-string",
        "duplicate",
        "case-duplicate",
        "padded-duplicate",
        "not-a-string",
        "null",
    ],
)
async def test_invalid_plan_raises_invalid_response(queries: list[object]) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await plan_queries(as_client(ScriptedLLMClient({"queries": queries})), TOPIC)


@pytest.mark.parametrize("reply", [{}, {"queries": "один, два, три"}, ["один"], "text", None])
async def test_wrong_reply_shape_raises_invalid_response(reply: object) -> None:
    with pytest.raises(LLMInvalidResponseError):
        await plan_queries(as_client(ScriptedLLMClient(reply)), TOPIC)


@pytest.mark.parametrize(
    "error",
    [LLMUnavailableError("down"), LLMInvalidResponseError("bad json"), RuntimeError("bug")],
)
async def test_llm_errors_propagate_unchanged(error: Exception) -> None:
    with pytest.raises(type(error)) as raised:
        await plan_queries(as_client(ScriptedLLMClient(error)), TOPIC)

    assert raised.value is error


@pytest.mark.parametrize("topic", ["", "   ", "\n\t"])
async def test_blank_topic_is_rejected_before_calling_the_llm(topic: str) -> None:
    fake = ScriptedLLMClient({"queries": VALID})

    with pytest.raises(ValueError, match="topic"):
        await plan_queries(as_client(fake), topic)

    assert fake.calls == []


async def test_request_carries_the_topic_the_schema_and_a_token_budget() -> None:
    fake = ScriptedLLMClient({"queries": VALID})

    await plan_queries(as_client(fake), f"  {TOPIC} \n")

    messages, schema, max_tokens = fake.calls[0]
    assert schema is QueryPlan
    assert max_tokens > 0
    assert messages[-1].role is Role.USER
    assert messages[-1].content == f"Topic: {TOPIC}"


def test_prompt_states_the_bounds_and_both_languages() -> None:
    system = render_query_planning(TOPIC)[0]

    assert system.role is Role.SYSTEM
    assert "between 3 and 5" in system.content
    assert "Russian" in system.content
    assert "English" in system.content


def test_schema_shown_to_the_model_carries_the_bounds() -> None:
    queries = QueryPlan.model_json_schema()["properties"]["queries"]

    assert queries["minItems"] == 3
    assert queries["maxItems"] == 5


def deepseek_reply(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


async def test_real_client_returns_a_valid_plan(
    make_factory: Callable[[], LLMClientFactory], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(DEEPSEEK_URL).mock(
        return_value=deepseek_reply(json.dumps({"queries": VALID}, ensure_ascii=False))
    )

    queries = await plan_queries(make_factory().get_client(LLMStep.QUERY_PLANNING), TOPIC)

    assert queries == VALID
    assert route.call_count == 1


async def test_real_client_rejects_a_plan_that_stays_invalid(
    make_factory: Callable[[], LLMClientFactory], respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(DEEPSEEK_URL).mock(
        return_value=deepseek_reply(json.dumps({"queries": ["один", "один", "два"]}))
    )

    with pytest.raises(LLMInvalidResponseError):
        await plan_queries(make_factory().get_client(LLMStep.QUERY_PLANNING), TOPIC)

    assert route.call_count == 3
    correction = json.loads(route.calls[1].request.content)["messages"][-1]["content"]
    assert "queries must be unique" in correction
