from collections.abc import Sequence

from app.domain.fact import FactSet
from app.prompts.writing import cautious_ids
from app.services.quote_check import extract_numbers
from app.services.short_post import dispute_units


def numbers_of(texts: Sequence[str]) -> set[str]:
    numbers: set[str] = set()
    for text in texts:
        numbers |= extract_numbers(text)
    return numbers


def disputed_with_stated_numbers(offered: FactSet, texts: Sequence[str]) -> set[str]:
    disputed = cautious_ids(offered)
    stated = numbers_of(texts)
    found: set[str] = set()
    for fact in offered.facts:
        numbers = extract_numbers(fact.text)
        if fact.id in disputed and numbers and numbers <= stated:
            found.add(fact.id)
    return found


def complete_dispute_groups(used: set[str], offered: FactSet) -> set[str]:
    completed = set(used)
    for group in dispute_units(offered, set()):
        if group & used:
            completed |= group
    return completed


def with_disputed_facts(
    reported: Sequence[str], texts: Sequence[str], offered: FactSet
) -> list[str]:
    used = set(reported) | disputed_with_stated_numbers(offered, texts)
    added = complete_dispute_groups(used, offered) - set(reported)
    return [*reported, *(fact.id for fact in offered.facts if fact.id in added)]
