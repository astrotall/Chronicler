from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.config.constants import RELEVANCE_ROTATION_MIN
from app.domain.fact import FactStatus, SourceRef, all_weak
from app.services.fact_relevance import FactRelevance

PRIORITY_CONFIRMED = 0
PRIORITY_SINGLE = 1
PRIORITY_WEAK_ONLY = 2


class Selectable(Protocol):
    @property
    def status(self) -> FactStatus: ...

    @property
    def support(self) -> Sequence[SourceRef]: ...


class Selection(BaseModel):
    model_config = ConfigDict(frozen=True)

    kept: tuple[int, ...]
    cut_by_limit: int = 0
    cut_by_domain_cap: int = 0
    restored_by_floor: int = 0
    set_aside_off_topic: int = 0
    off_topic_restored: int = 0


def fact_priority(item: Selectable) -> int:
    if item.status is FactStatus.CONFIRMED:
        return PRIORITY_CONFIRMED
    if all_weak(item.support):
        return PRIORITY_WEAK_ONLY
    return PRIORITY_SINGLE


type SortKey = tuple[int, int, int, int]


def rank_order(
    items: Sequence[Selectable],
    candidates: Sequence[int],
    relevance: Mapping[int, FactRelevance],
) -> list[int]:
    default = FactRelevance()
    seen: Counter[tuple[int, int, str]] = Counter()
    keys: dict[int, SortKey] = {}
    for position, index in enumerate(candidates):
        score = relevance.get(index, default)
        priority = fact_priority(items[index])
        rotation = 0
        if score.relevance >= RELEVANCE_ROTATION_MIN:
            group = (priority, score.relevance, score.aspect)
            rotation = seen[group]
            seen[group] += 1
        keys[index] = (priority, -score.relevance, rotation, position)
    return sorted(candidates, key=keys.__getitem__)


def cap_domains(support: Sequence[SourceRef]) -> list[str]:
    strong = [ref.domain for ref in support if not ref.weak]
    return list(dict.fromkeys(strong or [ref.domain for ref in support]))


def apply_domain_cap(
    ordered: Sequence[int], items: Sequence[Selectable], max_per_domain: int
) -> tuple[list[int], list[int]]:
    load: Counter[str] = Counter()
    admitted: list[int] = []
    displaced: list[int] = []
    for index in ordered:
        open_domains = [
            domain for domain in cap_domains(items[index].support) if load[domain] < max_per_domain
        ]
        if not open_domains:
            displaced.append(index)
            continue
        load[min(open_domains, key=lambda domain: load[domain])] += 1
        admitted.append(index)
    return admitted, displaced


def select_facts(
    items: Sequence[Selectable],
    candidates: Sequence[int],
    *,
    max_facts: int,
    floor: int,
    max_per_domain: int | None,
    relevance: Mapping[int, FactRelevance] | None = None,
) -> Selection:
    scores = relevance or {}
    off_topic = {index for index in candidates if index in scores and scores[index].off_topic}
    ordered = rank_order(items, [index for index in candidates if index not in off_topic], scores)
    aside = rank_order(items, [index for index in candidates if index in off_topic], scores)
    admitted, displaced = (
        (list(ordered), [])
        if max_per_domain is None
        else apply_domain_cap(ordered, items, max_per_domain)
    )
    kept = admitted[:max_facts]
    missing = max(min(floor, max_facts) - len(kept), 0)
    restored = displaced[:missing]
    returned = aside[: missing - len(restored)]
    rank = {index: position for position, index in enumerate(ordered + aside)}
    return Selection(
        kept=tuple(sorted(kept + restored + returned, key=rank.__getitem__)),
        cut_by_limit=len(admitted) - len(kept),
        cut_by_domain_cap=len(displaced) - len(restored),
        restored_by_floor=len(restored),
        set_aside_off_topic=len(aside) - len(returned),
        off_topic_restored=len(returned),
    )
