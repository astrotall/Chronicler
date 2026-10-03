from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactStatus(StrEnum):
    CONFIRMED = "confirmed"
    SINGLE = "single"
    DISPUTED = "disputed"


class ExtractionOutcome(StrEnum):
    EXTRACTED = "extracted"
    INSUFFICIENT = "insufficient_facts"


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    snippet_id: str
    url: str
    domain: str
    quote: str


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    support: list[SourceRef] = Field(min_length=1)
    status: FactStatus


class Dispute(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_ids: list[str] = Field(min_length=2)
    explanation: str


class FactSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str
    facts: list[Fact]
    disputes: list[Dispute]


class ExtractionStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    snippets: int = Field(default=0, ge=0)
    snippets_truncated: int = Field(default=0, ge=0)
    candidates: int = Field(default=0, ge=0)
    candidates_over_limit: int = Field(default=0, ge=0)
    support_proposed: int = Field(default=0, ge=0)
    support_unknown_snippet: int = Field(default=0, ge=0)
    support_too_short: int = Field(default=0, ge=0)
    support_not_found: int = Field(default=0, ge=0)
    support_number_mismatch: int = Field(default=0, ge=0)
    support_duplicate: int = Field(default=0, ge=0)
    facts_unsupported: int = Field(default=0, ge=0)
    facts_number_mismatch: int = Field(default=0, ge=0)
    facts_verified: int = Field(default=0, ge=0)
    disputes: int = Field(default=0, ge=0)
    disputes_withdrawn: int = Field(default=0, ge=0)
    dispute_unknown_ids: int = Field(default=0, ge=0)
    facts_disputed: int = Field(default=0, ge=0)
    facts_cut_by_limit: int = Field(default=0, ge=0)
    facts_kept: int = Field(default=0, ge=0)

    @property
    def support_dropped(self) -> int:
        return (
            self.support_unknown_snippet
            + self.support_too_short
            + self.support_not_found
            + self.support_number_mismatch
        )


class FactsExtracted(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal[ExtractionOutcome.EXTRACTED] = ExtractionOutcome.EXTRACTED
    fact_set: FactSet
    stats: ExtractionStats


class InsufficientFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Literal[ExtractionOutcome.INSUFFICIENT] = ExtractionOutcome.INSUFFICIENT
    fact_set: FactSet
    assertable_count: int = Field(ge=0)
    required: int = Field(ge=1)
    stats: ExtractionStats


type FactExtraction = FactsExtracted | InsufficientFacts
