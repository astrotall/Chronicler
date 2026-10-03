from datetime import UTC, datetime

import pytest
from app.domain.llm import Message, Role
from app.llm.json_reply import (
    describe_validation_error,
    extract_json,
    parse_reply,
    with_json_instruction,
)
from app.llm.transport import parse_retry_after
from app.prompts.json_reply import JSON_TRUNCATION_NOTE, render_json_correction
from pydantic import BaseModel, ValidationError

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


class Event(BaseModel):
    title: str
    year: int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('  \n{"a": 1}\n  ', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```JSON\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('```json {"a": 1}```', '{"a": 1}'),
        ('Вот ответ:\n```json\n{"a": 1}\n```\nГотово.', '{"a": 1}'),
        ('```json\r\n{"a": 1}\r\n```', '{"a": 1}'),
        ("", ""),
        ("```json\n```", ""),
    ],
    ids=[
        "plain",
        "padded",
        "json-fence",
        "upper-fence",
        "bare-fence",
        "inline-fence",
        "fence-in-prose",
        "crlf",
        "empty",
        "empty-fence",
    ],
)
def test_extract_json(text: str, expected: str) -> None:
    assert extract_json(text) == expected


def test_parse_reply_validates_against_the_schema() -> None:
    assert parse_reply('```json\n{"title": "Битва", "year": 1380}\n```', Event) == Event(
        title="Битва", year=1380
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Куликовская битва была в 1380 году",
        '{"title": "Битва", "year": 1380',
        '{"title": "Битва", "year": "тысяча триста восьмидесятый"}',
        '{"title": "Битва"}',
        '[{"title": "Битва", "year": 1380}]',
        'Ответ: {"title": "Битва", "year": 1380}',
    ],
    ids=["empty", "prose", "unclosed", "number-in-words", "missing", "array", "prose-prefix"],
)
def test_parse_reply_rejects_invalid_replies(text: str) -> None:
    with pytest.raises(ValidationError):
        parse_reply(text, Event)


def test_validation_problems_name_the_field_without_the_input() -> None:
    with pytest.raises(ValidationError) as raised:
        parse_reply('{"title": "секрет", "year": "давно"}', Event)

    problems = describe_validation_error(raised.value)

    assert len(problems) == 1
    assert problems[0].startswith("year: ")
    assert "секрет" not in problems[0]
    assert "давно" not in problems[0]


def test_invalid_json_problem_points_at_the_root() -> None:
    with pytest.raises(ValidationError) as raised:
        parse_reply("not json", Event)

    assert describe_validation_error(raised.value)[0].startswith("<root>: Invalid JSON")


def test_instruction_goes_after_leading_system_messages() -> None:
    messages = [
        Message(role=Role.SYSTEM, content="a"),
        Message(role=Role.USER, content="b"),
        Message(role=Role.SYSTEM, content="c"),
    ]

    result = with_json_instruction(messages, Event)

    assert [message.content for message in result][:1] == ["a"]
    assert result[1].role == Role.SYSTEM
    assert '"year"' in result[1].content
    assert [message.content for message in result][2:] == ["b", "c"]


def test_instruction_without_system_messages_comes_first() -> None:
    result = with_json_instruction([Message(role=Role.USER, content="b")], Event)

    assert result[0].role == Role.SYSTEM
    assert result[1].content == "b"


def test_correction_lists_problems_and_notes_truncation_only_when_cut() -> None:
    plain = render_json_correction(["year: Field required"], truncated=False)
    cut = render_json_correction(["<root>: Invalid JSON"], truncated=True)

    assert plain.role == Role.USER
    assert "- year: Field required" in plain.content
    assert JSON_TRUNCATION_NOTE not in plain.content
    assert JSON_TRUNCATION_NOTE in cut.content


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"retry-after": "5"}, 5.0),
        ({"retry-after": "0.5"}, 0.5),
        ({"retry-after": "-3"}, 0.0),
        ({"retry-after": "Sat, 03 Oct 2026 12:00:20 GMT"}, 20.0),
        ({"retry-after": "Sat, 03 Oct 2026 11:59:00 GMT"}, 0.0),
        ({"retry-after": "soon"}, None),
        ({"retry-after": ""}, None),
    ],
    ids=["absent", "seconds", "fraction", "negative", "date", "past-date", "garbage", "blank"],
)
def test_parse_retry_after(headers: dict[str, str], expected: float | None) -> None:
    assert parse_retry_after(headers, NOW) == expected
