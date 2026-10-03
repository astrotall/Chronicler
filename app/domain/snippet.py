import hashlib
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SNIPPET_ID_LENGTH = 16
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
PATH_SEPARATOR = "/"


class SnippetOrigin(StrEnum):
    WIKIPEDIA_RU = "wikipedia_ru"
    WIKIPEDIA_EN = "wikipedia_en"
    TAVILY = "tavily"


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip(PATH_SEPARATOR),
            parts.query,
            "",
        )
    )


def snippet_id(url: str) -> str:
    digest = hashlib.sha256(normalize_url(url).encode()).hexdigest()
    return digest[:SNIPPET_ID_LENGTH]


def url_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class Snippet(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default="", description="Derived from the url, any given value is replaced")
    origin: SnippetOrigin
    title: str
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    lang: str | None = None

    @model_validator(mode="before")
    @classmethod
    def assign_id(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("url"), str):
            return {**data, "id": snippet_id(data["url"])}
        return data

    @field_validator("url")
    @classmethod
    def require_web_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme.lower() not in ALLOWED_URL_SCHEMES or not parts.hostname:
            raise ValueError("url must be an absolute http or https url")
        return value
