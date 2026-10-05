from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.draft import Draft


class StyleRule(StrEnum):
    DASH = "dash"
    BANNED_PHRASE = "banned_phrase"
    INVENTED_EXPERIENCE = "invented_experience"
    EMOJI = "emoji"
    HASHTAG = "hashtag"
    CLOSING_QUESTION = "closing_question"
    LENGTH = "length"
    UNVERIFIED_NUMBER = "unverified_number"
    CLICHE = "cliche"
    TRIPLET = "triplet"
    FILLER = "filler"
    OPINION = "opinion"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"


class ViolationSource(StrEnum):
    CODE = "code"
    CRITIC = "critic"


class CriticStatus(StrEnum):
    CHECKED = "checked"
    DISABLED = "disabled"
    FAILED = "failed"


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: StyleRule
    source: ViolationSource
    part: int | None = Field(default=None, ge=1)
    excerpt: str | None = None
    explanation: str


class StyleReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    violations: list[Violation]
    critic: CriticStatus
    critic_dropped: int = Field(default=0, ge=0)
    critic_withdrawn: int = Field(default=0, ge=0)
    critic_over_limit: int = Field(default=0, ge=0)

    @property
    def passed(self) -> bool:
        return not self.violations


class StyleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: Draft
    report: StyleReport
    attempts: int = Field(ge=1)
    chosen_attempt: int = Field(ge=1)
    regenerations: int = Field(ge=0)
    regeneration_failed: bool = False
    regressions_rejected: int = Field(default=0, ge=0)
    unfixed_per_round: list[int] = Field(default_factory=list)
    attribution_kept: int = Field(default=0, ge=0)
    unreported_survivors: int = Field(default=0, ge=0)
    removal_blocked: int = Field(default=0, ge=0)
