from collections.abc import Sequence

from app.config import stance as stance_config
from app.config.constants import STYLE_ATTRIBUTION_MIN_FACT_OVERLAP
from app.domain.fact import FactSet
from app.domain.style import Violation
from app.prompts.writing import cautious_ids
from app.services.claim_survival import content_stems, is_deletable, sentence_of, stem_overlap
from app.services.stance import find_marker

ATTRIBUTION_MARKERS: tuple[str, ...] = stance_config.CLAIM_MARKERS + stance_config.REBUTTAL_MARKERS


def is_attribution_wording(violation: Violation, texts: Sequence[str], offered: FactSet) -> bool:
    if violation.excerpt is None or violation.part is None:
        return False
    sentence = sentence_of(violation.excerpt, texts[violation.part - 1])
    if find_marker(sentence, ATTRIBUTION_MARKERS) is None:
        return False
    stems = content_stems(sentence)
    cautious = cautious_ids(offered)
    cautious_best = 0.0
    asserted_best = 0.0
    for fact in offered.facts:
        overlap = stem_overlap(stems, content_stems(fact.text))
        if fact.id in cautious:
            cautious_best = max(cautious_best, overlap)
        else:
            asserted_best = max(asserted_best, overlap)
    return cautious_best >= STYLE_ATTRIBUTION_MIN_FACT_OVERLAP and cautious_best >= asserted_best


def attribution_exempt(
    violations: Sequence[Violation], texts: Sequence[str], offered: FactSet
) -> frozenset[Violation]:
    return frozenset(
        violation
        for violation in violations
        if is_deletable(violation) and is_attribution_wording(violation, texts, offered)
    )
