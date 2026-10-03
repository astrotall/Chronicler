import re
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from app.domain.llm import Message, Role
from app.prompts.json_reply import render_json_instruction

FENCED_BLOCK = re.compile(r"```[ \t]*(?:json)?[ \t]*\r?\n?(.*?)```", re.DOTALL | re.IGNORECASE)
ROOT_LOCATION = "<root>"
LOCATION_SEPARATOR = "."


def extract_json(text: str) -> str:
    match = FENCED_BLOCK.search(text)
    if match is None:
        return text.strip()
    return match.group(1).strip()


def parse_reply[T: BaseModel](text: str, schema: type[T]) -> T:
    return schema.model_validate_json(extract_json(text))


def describe_validation_error(error: ValidationError) -> list[str]:
    return [
        f"{LOCATION_SEPARATOR.join(str(part) for part in problem['loc']) or ROOT_LOCATION}: "
        f"{problem['msg']}"
        for problem in error.errors(include_url=False, include_context=False, include_input=False)
    ]


def with_json_instruction(messages: Sequence[Message], schema: type[BaseModel]) -> list[Message]:
    split = next(
        (index for index, message in enumerate(messages) if message.role != Role.SYSTEM),
        len(messages),
    )
    return [*messages[:split], render_json_instruction(schema), *messages[split:]]
