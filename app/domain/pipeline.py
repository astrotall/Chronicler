from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.draft import Draft
from app.domain.fact import ExtractionStats, FactSet, InsufficientFacts
from app.domain.research import SourceFailure
from app.domain.style import StyleResult


class PipelineStage(StrEnum):
    PLANNING = "planning"
    RESEARCH = "research"
    FACTS = "facts"
    WRITING = "writing"
    STYLE = "style"


class PostAction(StrEnum):
    SHORTER = "shorter"
    THREAD = "thread"
    ANGLE = "angle"
    VARIANT = "variant"


class FailureKind(StrEnum):
    CONFIG = "config"
    AUTH = "auth"
    REQUEST = "request"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"


class StoredDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    run_id: str
    fact_set: FactSet
    draft: Draft
    angle_index: int | None = Field(default=None, ge=0)


class ThreadDowngrade(BaseModel):
    model_config = ConfigDict(frozen=True)

    assertable: int = Field(ge=0)
    required: int = Field(ge=1)


class PostReady(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    result: StyleResult
    fact_set: FactSet
    actions: list[PostAction]
    variant: bool = False
    failures: list[SourceFailure] = Field(default_factory=list)
    downgrade: ThreadDowngrade | None = None
    stats: ExtractionStats | None = None


class NoSources(BaseModel):
    model_config = ConfigDict(frozen=True)


class ResearchFailed(BaseModel):
    model_config = ConfigDict(frozen=True)

    failures: list[SourceFailure]


class NothingFound(BaseModel):
    model_config = ConfigDict(frozen=True)

    failures: list[SourceFailure]


class NotEnoughFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    extraction: InsufficientFacts
    disputed: int = Field(ge=0)
    failures: list[SourceFailure]


class StepFailed(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: PipelineStage
    kind: FailureKind


class DraftExpired(BaseModel):
    model_config = ConfigDict(frozen=True)


class ThreadUnavailable(BaseModel):
    model_config = ConfigDict(frozen=True)

    assertable: int = Field(ge=0)
    required: int = Field(ge=1)


type TopicOutcome = (
    PostReady | NoSources | ResearchFailed | NothingFound | NotEnoughFacts | StepFailed
)
type ReworkOutcome = PostReady | DraftExpired | ThreadUnavailable | StepFailed
