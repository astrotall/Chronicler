from app.domain.fact import ClaimStance, FactSet
from app.prompts.writing import assertable_facts, attributed_ids, disputed_ids
from app.services.short_post import dispute_units, restrict_to


def attributed_units(fact_set: FactSet, chosen: set[str]) -> list[set[str]]:
    order = {fact.id: index for index, fact in enumerate(fact_set.facts)}
    assertable = {fact.id for fact in assertable_facts(fact_set)}
    attributed = attributed_ids(fact_set)
    units = dispute_units(fact_set, disputed_ids(fact_set))
    for fact in fact_set.facts:
        if fact.id not in attributed:
            continue
        if fact.stance is ClaimStance.CLAIMED:
            units.append({fact.id})
            continue
        rebuttals = {fact_id for fact_id in fact.rebutted_by if fact_id in assertable}
        if rebuttals:
            units.append({fact.id} | (rebuttals - chosen))
    return sorted(units, key=lambda unit: min(order[fact_id] for fact_id in unit))


def select_thread_facts(fact_set: FactSet, max_facts: int, max_attributed: int) -> FactSet:
    if not max_facts:
        return fact_set
    chosen = {fact.id for fact in assertable_facts(fact_set)[:max_facts]}
    for unit in attributed_units(fact_set, chosen)[:max_attributed]:
        chosen |= unit
    if not chosen:
        return fact_set
    return restrict_to(fact_set, chosen)
