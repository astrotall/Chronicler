import re
from itertools import pairwise

from app.config.constants import SENTENCE_ABBREVIATIONS
from app.domain.draft import SentenceBudget
from app.domain.fact import Fact, FactSet, FactStatus
from app.prompts.writing import disputed_ids
from app.services.quote_check import extract_numbers

SENTENCE_BOUNDARY = re.compile(r"[.!?…]+[\"'»”)]*(?=\s+[«\"„(]?[0-9A-ZА-ЯЁ])")
WORD_BEFORE = re.compile(r"(\w+)$")
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
ABBREVIATION_POINT = "."
STATUS_PRIORITY: dict[FactStatus, int] = {FactStatus.CONFIRMED: 0, FactStatus.SINGLE: 1}


def sentence_budget(max_chars: int, sentence_chars: int) -> SentenceBudget:
    return SentenceBudget(
        max_sentences=max(1, max_chars // sentence_chars),
        sentence_chars=min(sentence_chars, max_chars),
    )


def dispute_units(fact_set: FactSet, disputed: set[str]) -> list[set[str]]:
    known = {fact.id for fact in fact_set.facts}
    units: list[set[str]] = []
    for dispute in fact_set.disputes:
        members = {fact_id for fact_id in dispute.fact_ids if fact_id in known}
        touching = [unit for unit in units if unit & members]
        merged = members.union(*touching)
        units = [unit for unit in units if not unit & members]
        if merged:
            units.append(merged)
    grouped = set().union(*units)
    ungrouped = {fact_id for fact_id in disputed & known if fact_id not in grouped}
    if ungrouped:
        units.append(ungrouped)
    order = {fact.id: index for index, fact in enumerate(fact_set.facts)}
    return sorted(units, key=lambda unit: min(order[fact_id] for fact_id in unit))


def status_rank(fact: Fact) -> int:
    return STATUS_PRIORITY.get(fact.status, len(STATUS_PRIORITY))


def select_short_facts(fact_set: FactSet, max_facts: int) -> FactSet:
    disputed = disputed_ids(fact_set)
    assertable = sorted(
        (fact for fact in fact_set.facts if fact.id not in disputed), key=status_rank
    )
    chosen = {fact.id for fact in assertable[:max_facts]}
    units = dispute_units(fact_set, disputed)
    for unit in units:
        if len(chosen) + len(unit) <= max_facts:
            chosen |= unit
    if not chosen and units:
        chosen = units[0]
    return FactSet(
        topic=fact_set.topic,
        facts=[fact for fact in fact_set.facts if fact.id in chosen],
        disputes=[
            dispute
            for dispute in fact_set.disputes
            if any(fact_id in chosen for fact_id in dispute.fact_ids)
        ],
    )


def is_abbreviation(text: str, position: int) -> bool:
    if text[position] != ABBREVIATION_POINT:
        return False
    word = WORD_BEFORE.search(text[:position])
    if word is None:
        return False
    token = word.group(1)
    return (len(token) == 1 and token.isalpha()) or token.lower() in SENTENCE_ABBREVIATIONS


def sentence_ends(text: str) -> list[int]:
    return [
        match.end()
        for match in SENTENCE_BOUNDARY.finditer(text)
        if not is_abbreviation(text, match.start())
    ]


def split_sentences(text: str) -> list[str]:
    bounds = [0, *sentence_ends(text), len(text)]
    pieces = (text[start:end].strip() for start, end in pairwise(bounds))
    return [piece for piece in pieces if piece]


def sentences_to_cut(text: str, excess: int) -> list[str]:
    sentences = split_sentences(text)
    ranked = sorted(range(len(sentences)), key=lambda index: len(sentences[index]), reverse=True)
    chosen: set[int] = set()
    covered = 0
    for index in ranked:
        if covered >= excess:
            break
        chosen.add(index)
        covered += len(sentences[index])
    return [sentence for index, sentence in enumerate(sentences) if index in chosen]


def fits(text: str, end: int, max_chars: int) -> bool:
    return len(text[:end].rstrip()) <= max_chars


def drop_tail(text: str, max_chars: int) -> tuple[str, list[str]] | None:
    paragraph_ends = [match.start() for match in PARAGRAPH_BREAK.finditer(text)]
    cuts = [end for end in paragraph_ends if fits(text, end, max_chars)]
    if not cuts:
        first_paragraph = text[: paragraph_ends[0]] if paragraph_ends else text
        cuts = [end for end in sentence_ends(first_paragraph) if fits(text, end, max_chars)]
    if not cuts:
        return None
    cut = cuts[-1]
    pieces = (piece.strip() for piece in PARAGRAPH_BREAK.split(text[cut:]))
    return text[:cut].rstrip(), [piece for piece in pieces if piece]


def mentions_disputed_numbers(text: str, fact_set: FactSet) -> bool:
    disputed = disputed_ids(fact_set)
    numbers: set[str] = set()
    for fact in fact_set.facts:
        if fact.id in disputed:
            numbers |= extract_numbers(fact.text)
    return bool(extract_numbers(text) & numbers)
