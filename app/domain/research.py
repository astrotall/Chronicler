from pydantic import BaseModel, ConfigDict

from app.domain.snippet import Snippet


class SourceFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    query: str
    kind: str
    reason: str


class ResearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    snippets: list[Snippet]
    failures: list[SourceFailure]
