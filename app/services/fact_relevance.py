import re
from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config.constants import (
    DECADE_SPAN_YEARS,
    RELEVANCE_MAX,
    RELEVANCE_MAX_ASPECTS,
    RELEVANCE_MIN,
    RELEVANCE_OTHER_ASPECT,
    RELEVANCE_PERIOD_MARGIN_YEARS,
    RELEVANCE_UNSCORED,
    RELEVANCE_YEAR_MAX,
    RELEVANCE_YEAR_MIN,
)
from app.services.quote_check import extract_numbers

DECADE_PATTERN = re.compile(r"(?<!\d)(\d{3}0)\s*[-‐‑–]?\s*(?:ые|ых|е|х|s)(?!\w)", re.IGNORECASE)

type EntryId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RelevanceEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: EntryId
    aspect: str = ""
    about_source: bool = False
    outside_period: bool = False
    relevance: int


class RelevanceReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    facts: list[RelevanceEntry]


class FactRelevance(BaseModel):
    model_config = ConfigDict(frozen=True)

    relevance: int = RELEVANCE_UNSCORED
    aspect: str = RELEVANCE_OTHER_ASPECT
    about_source: bool = False
    outside_period: bool = False

    @property
    def unrelated(self) -> bool:
        return self.relevance == RELEVANCE_MIN

    @property
    def off_topic(self) -> bool:
        return self.about_source or self.outside_period or self.unrelated


class RelevanceCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    scores: dict[int, FactRelevance] = Field(default_factory=dict)
    dropped: int = 0
    failed: bool = False
    period_overruled: int = 0


class Period(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: int
    end: int

    def holds(self, year: int) -> bool:
        return self.start <= year <= self.end


def in_range(relevance: int) -> bool:
    return RELEVANCE_MIN <= relevance <= RELEVANCE_MAX


def normalize_aspect(aspect: str) -> str:
    return " ".join(aspect.split()).casefold() or RELEVANCE_OTHER_ASPECT


def cap_aspects(scores: Mapping[int, FactRelevance]) -> dict[int, FactRelevance]:
    allowed: list[str] = []
    capped: dict[int, FactRelevance] = {}
    for index in sorted(scores):
        score = scores[index]
        if score.aspect not in allowed and len(allowed) < RELEVANCE_MAX_ASPECTS:
            allowed.append(score.aspect)
        aspect = score.aspect if score.aspect in allowed else RELEVANCE_OTHER_ASPECT
        capped[index] = score.model_copy(update={"aspect": aspect})
    return capped


def score_of(entry: RelevanceEntry) -> FactRelevance:
    return FactRelevance(
        relevance=entry.relevance,
        aspect=normalize_aspect(entry.aspect),
        about_source=entry.about_source,
        outside_period=entry.outside_period,
    )


def years_in(text: str) -> set[int]:
    return {
        int(number)
        for number in extract_numbers(text)
        if number.isdigit() and RELEVANCE_YEAR_MIN <= int(number) <= RELEVANCE_YEAR_MAX
    }


def topic_period(topic: str) -> Period | None:
    years = years_in(topic)
    if not years:
        return None
    decade_ends = {
        int(match.group(1)) + DECADE_SPAN_YEARS for match in DECADE_PATTERN.finditer(topic)
    }
    return Period(
        start=min(years) - RELEVANCE_PERIOD_MARGIN_YEARS,
        end=max(years | decade_ends) + RELEVANCE_PERIOD_MARGIN_YEARS,
    )


def dated_outside(text: str, period: Period | None) -> bool:
    years = years_in(text)
    return period is not None and bool(years) and not any(period.holds(year) for year in years)


def check_period(check: RelevanceCheck, topic: str, texts: Sequence[str]) -> RelevanceCheck:
    period = topic_period(topic)
    scores: dict[int, FactRelevance] = {}
    overruled = 0
    for index, score in check.scores.items():
        if score.outside_period and not dated_outside(texts[index], period):
            overruled += 1
            score = score.model_copy(update={"outside_period": False})
        scores[index] = score
    return check.model_copy(update={"scores": scores, "period_overruled": overruled})
