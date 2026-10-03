from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.domain.fact import FactStatus, SourceRef, all_weak

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


def fact_priority(item: Selectable) -> int:
    if item.status is FactStatus.CONFIRMED:
        return PRIORITY_CONFIRMED
    if all_weak(item.support):
        return PRIORITY_WEAK_ONLY
    return PRIORITY_SINGLE


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
) -> Selection:
    ordered = sorted(candidates, key=lambda index: fact_priority(items[index]))
    admitted, displaced = (
        (list(ordered), [])
        if max_per_domain is None
        else apply_domain_cap(ordered, items, max_per_domain)
    )
    kept = admitted[:max_facts]
    missing = max(min(floor, max_facts) - len(kept), 0)
    restored = displaced[:missing]
    rank = {index: position for position, index in enumerate(ordered)}
    return Selection(
        kept=tuple(sorted(kept + restored, key=rank.__getitem__)),
        cut_by_limit=len(admitted) - len(kept),
        cut_by_domain_cap=len(displaced) - len(restored),
        restored_by_floor=len(restored),
    )
