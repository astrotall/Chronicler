from app.domain.fact import ClaimStance, Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.style import StyleRule, Violation, ViolationSource
from app.services.attribution_exemption import attribution_exempt, is_attribution_wording

LEGEND = "По преданию, перед битвой Пересвет бился с Челубеем, и оба пали на поле."
PLAIN = "Перед битвой Пересвет бился с Челубеем, и оба пали на поле."
WITH_QUALIFIER = "По преданию, перед битвой Пересвет бился с Челубеем, и оба пали на поле."


def make_fact(
    fact_id: str,
    text: str,
    stance: ClaimStance = ClaimStance.ASSERTED,
    status: FactStatus = FactStatus.SINGLE,
) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="s", url="https://example.org", domain="example.org", quote=text)
        ],
        status=status,
        stance=stance,
    )


def flagged(
    excerpt: str, rule: StyleRule = StyleRule.UNSUPPORTED_CLAIM, part: int | None = 1
) -> Violation:
    return Violation(
        rule=rule,
        source=ViolationSource.CRITIC,
        part=part,
        excerpt=excerpt,
        explanation="Пояснение.",
    )


CLAIMED_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        make_fact("F1", "Куликовская битва произошла 8 сентября 1380 года."),
        make_fact("F2", LEGEND, ClaimStance.CLAIMED),
    ],
    disputes=[],
)
ASSERTED_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        make_fact("F1", "Куликовская битва произошла 8 сентября 1380 года."),
        make_fact("F2", PLAIN),
        make_fact(
            "F3",
            "По слухам, Мамай ждал союзника у реки Оки с большим войском.",
            ClaimStance.CLAIMED,
        ),
    ],
    disputes=[],
)


def test_attribution_of_a_claimed_fact_is_exempt() -> None:
    assert is_attribution_wording(flagged(LEGEND), [LEGEND], CLAIMED_SET)


def test_the_same_words_on_an_asserted_fact_are_not_exempt() -> None:
    assert not is_attribution_wording(flagged(WITH_QUALIFIER), [WITH_QUALIFIER], ASSERTED_SET)


def test_a_rebutted_fact_is_exempt() -> None:
    rebutted = FactSet(
        topic="Куликовская битва",
        facts=[make_fact("F2", LEGEND, ClaimStance.REBUTTED)],
        disputes=[],
    )

    assert is_attribution_wording(flagged(LEGEND), [LEGEND], rebutted)


def test_a_disputed_fact_is_exempt() -> None:
    text = (
        "Источники расходятся, принято считать, что численность русского войска составляла "
        "150 000 человек."
    )
    disputed = FactSet(
        topic="Куликовская битва",
        facts=[
            make_fact(
                "F2",
                "Численность русского войска составляла 150 000 человек.",
                status=FactStatus.DISPUTED,
            ),
            make_fact(
                "F3",
                "Численность русского войска оценивают в 60 000 человек.",
                status=FactStatus.DISPUTED,
            ),
        ],
        disputes=[Dispute(fact_ids=["F2", "F3"], explanation="Оценки расходятся.")],
    )

    assert is_attribution_wording(flagged(text), [text], disputed)


def test_a_sentence_without_a_marker_is_not_exempt_even_for_a_claimed_fact() -> None:
    assert not is_attribution_wording(flagged(PLAIN), [PLAIN], CLAIMED_SET)


def test_the_marker_is_found_in_the_sentence_around_a_short_excerpt() -> None:
    assert is_attribution_wording(flagged("бился с Челубеем"), [LEGEND], CLAIMED_SET)


def test_a_marker_with_no_matching_fact_is_not_exempt() -> None:
    text = "По преданию, Ермак покорил Сибирь с небольшим отрядом казаков."

    assert not is_attribution_wording(flagged(text), [text], CLAIMED_SET)


def test_a_finding_without_an_excerpt_or_part_is_not_exempt() -> None:
    no_excerpt = Violation(
        rule=StyleRule.UNSUPPORTED_CLAIM,
        source=ViolationSource.CRITIC,
        part=1,
        explanation="Пояснение.",
    )

    assert not is_attribution_wording(no_excerpt, [LEGEND], CLAIMED_SET)
    assert not is_attribution_wording(flagged(LEGEND, part=None), [LEGEND], CLAIMED_SET)


def test_only_deletable_rules_are_collected() -> None:
    opinion = flagged(LEGEND, StyleRule.OPINION)
    claim = flagged(LEGEND)

    assert attribution_exempt([opinion, claim], [LEGEND], CLAIMED_SET) == {claim}
