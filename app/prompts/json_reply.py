import json
from collections.abc import Sequence

from pydantic import BaseModel

from app.domain.llm import Message, Role

JSON_INSTRUCTION_TEMPLATE = (
    "Reply with a single json object and nothing else: no text before or after it, "
    "no markdown. The object must match this JSON Schema:\n{schema}"
)
JSON_CORRECTION_TEMPLATE = (
    "Your previous reply is not a valid json object for the required schema. Problems:\n"
    "{problems}\n"
    "Reply again with the corrected json object only."
)
JSON_TRUNCATION_NOTE = (
    "Your previous reply was cut off because it hit the output token limit. "
    "Make the json object shorter so that it fits: fewer items, shorter strings."
)
PROBLEM_LINE_PREFIX = "- "


def render_json_instruction(schema: type[BaseModel]) -> Message:
    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return Message(role=Role.SYSTEM, content=JSON_INSTRUCTION_TEMPLATE.format(schema=schema_text))


def render_json_correction(problems: Sequence[str], *, truncated: bool) -> Message:
    problem_lines = "\n".join(f"{PROBLEM_LINE_PREFIX}{problem}" for problem in problems)
    parts = [JSON_CORRECTION_TEMPLATE.format(problems=problem_lines)]
    if truncated:
        parts.append(JSON_TRUNCATION_NOTE)
    return Message(role=Role.USER, content="\n\n".join(parts))
