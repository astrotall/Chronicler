from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

type DraftText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PostFormat(StrEnum):
    SHORT = "short"
    LONG = "long"
    THREAD = "thread"


SINGLE_PART_FORMATS = frozenset({PostFormat.SHORT, PostFormat.LONG})


class DraftPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: DraftText
    prefix: str = ""

    @property
    def rendered(self) -> str:
        return f"{self.prefix}{self.text}"


class LengthIssue(StrEnum):
    PART_TOO_LONG = "part_too_long"
    TOO_MANY_PARTS = "too_many_parts"
    TOO_SHORT = "too_short"
    TOO_FEW_FACTS = "too_few_facts"
    TOO_FEW_PARTS = "too_few_parts"


class LengthViolation(BaseModel):
    model_config = ConfigDict(frozen=True)

    issue: LengthIssue
    part: int | None = Field(default=None, ge=1)
    actual: int = Field(ge=0)
    limit: int = Field(ge=0)


class SentenceBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_sentences: int = Field(ge=1)
    sentence_chars: int = Field(ge=1)


class LongSize(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_chars: int = Field(ge=0)
    min_facts: int = Field(ge=0)


class ThreadSize(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_tweets: int = Field(ge=0)
    min_facts: int = Field(ge=0)


class Revision(BaseModel):
    model_config = ConfigDict(frozen=True)

    instruction: DraftText
    previous: list[DraftText] = Field(min_length=1)


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)

    post_format: PostFormat
    parts: list[DraftPart] = Field(min_length=1)
    used_fact_ids: list[str]
    unverified_numbers: list[str]
    length_violations: list[LengthViolation]
    attempts: int = Field(ge=1)
    dropped_tail: list[DraftText] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_one_part_for_a_single_post(self) -> Self:
        if self.post_format in SINGLE_PART_FORMATS and len(self.parts) != 1:
            raise ValueError("a short or long post has exactly one part")
        return self

    @property
    def texts(self) -> list[str]:
        return [part.text for part in self.parts]

    @property
    def rendered(self) -> list[str]:
        return [part.rendered for part in self.parts]
