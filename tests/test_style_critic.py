import pytest
from app.config.constants import STYLE_CRITIC_EXPLANATION_MAX_CHARS
from app.domain.llm import Role
from app.domain.style import StyleRule, ViolationSource
from app.llm.errors import LLMInvalidResponseError
from app.prompts.style_rules import render_style_rules
from app.services.style_critic import CriticReply, critique_draft

from llm_helpers import ScriptedLLMClient, as_client
from style_helpers import (
    FACT_SET,
    NO_FINDINGS,
    UNIQUE_QUOTE,
    UNIQUE_URL_MARK,
    finding,
    findings,
)

FILLER = "Это решило многое."
UNSUPPORTED = "Место известно точно"
RETELLING = "Перед битвой Пересвет бился с Челубеем."
THREAD = [
    "8 сентября 1380 года у Непрядвы сошлись войска. " + FILLER,
    UNSUPPORTED + ": Куликово поле, у впадения Непрядвы в Дон.",
    RETELLING,
]


async def test_a_filler_phrase_is_found_with_its_part() -> None:
    client = ScriptedLLMClient(findings(finding(FILLER, "filler", "Оценка без факта.")))

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    [violation] = outcome.violations
    assert violation.rule is StyleRule.FILLER
    assert violation.source is ViolationSource.CRITIC
    assert violation.part == 1
    assert violation.excerpt == FILLER
    assert violation.explanation == "Оценка без факта."


async def test_an_unsupported_claim_is_found() -> None:
    client = ScriptedLLMClient(
        findings(finding(UNSUPPORTED, "unsupported_claim", "В фактах нет, что место точно."))
    )

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    [violation] = outcome.violations
    assert violation.rule is StyleRule.UNSUPPORTED_CLAIM
    assert violation.part == 2


async def test_an_honest_retelling_withdrawn_by_the_critic_is_not_a_violation() -> None:
    client = ScriptedLLMClient(
        findings(finding(RETELLING, "unsupported_claim", "Это пересказ факта F4.", False))
    )

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert outcome.violations == []
    assert outcome.withdrawn == 1


async def test_the_prompt_names_honest_retelling_as_an_exception() -> None:
    client = ScriptedLLMClient(NO_FINDINGS)

    await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    [(messages, schema, _)] = client.calls
    system = messages[0].content
    assert schema is CriticReply
    assert "These are not defects, never report them" in system
    assert "an honest retelling of a fact in other words" in system
    assert "«Армстронг, Олдрин и Коллинз» is fine" in system


async def test_an_excerpt_that_is_not_in_the_draft_is_dropped_and_counted() -> None:
    client = ScriptedLLMClient(
        findings(
            finding("Эта фраза придумана критиком", "cliche"),
            finding(FILLER, "filler"),
        )
    )

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert [violation.excerpt for violation in outcome.violations] == [FILLER]
    assert outcome.dropped == 1


async def test_an_excerpt_matches_ignoring_case_spaces_quotes_and_yo() -> None:
    texts = ["Сражение у «Непрядвы»   шло  весь день. Её берег стал полем."]
    client = ScriptedLLMClient(findings(finding('сражение у "непрядвы" шло весь день', "cliche")))

    outcome = await critique_draft(as_client(client), texts, FACT_SET, 10)

    [violation] = outcome.violations
    assert violation.part == 1


async def test_an_almost_matching_excerpt_is_dropped() -> None:
    client = ScriptedLLMClient(findings(finding("Это решило очень многое.", "filler")))

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert outcome.violations == []
    assert outcome.dropped == 1


async def test_a_too_short_excerpt_is_dropped() -> None:
    client = ScriptedLLMClient(findings(finding("Эт", "filler")))

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert outcome.violations == []
    assert outcome.dropped == 1


async def test_findings_over_the_limit_are_cut_and_counted() -> None:
    client = ScriptedLLMClient(
        findings(
            finding(FILLER, "filler"),
            finding(UNSUPPORTED, "unsupported_claim"),
            finding(RETELLING, "cliche"),
        )
    )

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 2)

    assert [violation.rule for violation in outcome.violations] == [
        StyleRule.FILLER,
        StyleRule.UNSUPPORTED_CLAIM,
    ]
    assert outcome.over_limit == 1


async def test_a_repeated_finding_is_kept_once() -> None:
    client = ScriptedLLMClient(findings(finding(FILLER, "filler"), finding(FILLER, "filler")))

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert len(outcome.violations) == 1


async def test_an_empty_list_of_findings_is_no_violation() -> None:
    client = ScriptedLLMClient(NO_FINDINGS)

    outcome = await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    assert outcome.violations == []
    assert (outcome.dropped, outcome.withdrawn, outcome.over_limit) == (0, 0, 0)


@pytest.mark.parametrize(
    "reply",
    [
        {"findings": "нет"},
        findings(finding(FILLER, "dash")),
        findings(finding(FILLER, "filler", "Я" * (STYLE_CRITIC_EXPLANATION_MAX_CHARS + 1))),
        {"something": []},
    ],
)
async def test_a_reply_of_the_wrong_shape_is_invalid(reply: object) -> None:
    client = ScriptedLLMClient(reply)

    with pytest.raises(LLMInvalidResponseError):
        await critique_draft(as_client(client), THREAD, FACT_SET, 10)


async def test_the_critic_sees_parts_rules_facts_and_disputes_but_no_sources() -> None:
    client = ScriptedLLMClient(NO_FINDINGS)

    await critique_draft(as_client(client), THREAD, FACT_SET, 10)

    [(messages, _, _)] = client.calls
    assert [message.role for message in messages] == [Role.SYSTEM, Role.USER]
    assert render_style_rules() in messages[0].content
    user = messages[1].content
    assert "[1]\n" + THREAD[0] in user
    assert "[3]\n" + RETELLING in user
    assert "F1 [confirmed]: Куликовская битва произошла 8 сентября 1380 года." in user
    assert "Disagreement: Оценки численности войска расходятся." in user
    assert "F5: Численность русского войска оценивают в 60 000 человек." in user
    assert UNIQUE_QUOTE not in user
    assert UNIQUE_URL_MARK not in user


async def test_a_single_post_is_shown_without_numbering() -> None:
    client = ScriptedLLMClient(NO_FINDINGS)

    await critique_draft(as_client(client), ["Битва шла у Дона."], FACT_SET, 10)

    [(messages, _, _)] = client.calls
    assert messages[1].content.endswith("Post:\nБитва шла у Дона.")
