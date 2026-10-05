import logging

import pytest
from app.config import style
from app.config.settings import Settings
from app.domain.draft import LengthIssue, LengthViolation, PostFormat
from app.domain.llm import Message
from app.domain.style import CriticStatus, StyleResult, StyleRule
from app.llm.errors import LLMInvalidResponseError, LLMUnavailableError
from app.services.generator import SingleReply, ThreadReply
from app.services.style_review import StyleLimits, review_style

from llm_helpers import ScriptedLLMClient, as_client
from style_helpers import (
    FACT_SET,
    NO_FINDINGS,
    finding,
    findings,
    make_draft,
    make_style_limits,
    make_writing_limits,
    single_reply,
    thread_reply,
)

CLEAN = "8 сентября 1380 года на Куликовом поле сошлись войска."
WITH_DASH = "8 сентября 1380 года — на Куликовом поле сошлись войска."
FILLER = "Это решило многое."
WITH_FILLER = f"{CLEAN} {FILLER}"
UNSUPPORTED = "Место известно точно."
WITH_UNSUPPORTED = f"{CLEAN} {UNSUPPORTED}"
CLICHE = "Эпоха сделала выбор."
WITH_CLICHE_AND_DASH = f"{CLEAN} {CLICHE} Победа — начало."


async def review(
    writer: ScriptedLLMClient,
    critic: ScriptedLLMClient,
    text: str,
    *,
    limits: StyleLimits | None = None,
    allow_closing_question: bool = False,
    angle: str | None = None,
) -> StyleResult:
    return await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([text]),
        FACT_SET,
        make_writing_limits(),
        limits or make_style_limits(),
        angle=angle,
        allow_closing_question=allow_closing_question,
    )


def revision_prompt(writer: ScriptedLLMClient, index: int) -> str:
    messages: list[Message] = writer.calls[index][0]
    return messages[-1].content


async def test_a_clean_draft_passes_with_one_critic_call_and_no_writer_call() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(NO_FINDINGS)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([CLEAN]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert writer.calls == []
    assert len(critic.calls) == 1
    assert result.report.passed
    assert result.report.critic is CriticStatus.CHECKED
    assert (result.attempts, result.chosen_attempt, result.regenerations) == (1, 1, 0)
    assert result.draft.texts == [CLEAN]
    assert not result.regeneration_failed


async def test_a_violation_is_fixed_at_the_second_attempt() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")), NO_FINDINGS)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([WITH_FILLER]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert len(writer.calls) == 1
    assert writer.calls[0][1] is SingleReply
    assert len(critic.calls) == 2
    assert result.report.passed
    assert result.draft.texts == [CLEAN]
    assert (result.attempts, result.chosen_attempt, result.regenerations) == (2, 2, 1)


async def test_the_revision_names_each_violation_and_keeps_the_previous_text() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(findings(finding(CLICHE, "cliche", "Штамп без факта.")), NO_FINDINGS)

    await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([WITH_CLICHE_AND_DASH]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
        angle="о цене победы",
    )

    prompt = revision_prompt(writer, 0)
    assert f"Previous version of the post:\n{WITH_CLICHE_AND_DASH}" in prompt
    assert "Fix only the flagged fragments below." in prompt
    assert "The rest of the text stays word for word, the same length, the same facts" in prompt
    assert "- part 1: «сделала выбор. Победа — начало.»: Длинное или среднее тире (—)" in prompt
    assert f"- part 1: «{CLICHE}»: Штамп без факта." in prompt
    assert "Angle requested by the author: о цене победы" in prompt


async def test_found_phrases_are_forbidden_in_every_later_attempt() -> None:
    writer = ScriptedLLMClient(single_reply(WITH_UNSUPPORTED), single_reply(CLEAN))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler")),
        findings(finding(UNSUPPORTED, "unsupported_claim")),
        NO_FINDINGS,
    )

    result = await review(writer, critic, WITH_FILLER)

    assert result.report.passed
    first = revision_prompt(writer, 0)
    second = revision_prompt(writer, 1)
    assert f"Do not use these phrases or close variants of them: «{FILLER}»." in first
    assert f"«{FILLER}», «{UNSUPPORTED}»" in second


async def test_a_dash_is_not_listed_as_a_forbidden_phrase() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(NO_FINDINGS, NO_FINDINGS)

    await review(writer, critic, WITH_DASH)

    assert "Do not use these phrases" not in revision_prompt(writer, 0)


async def test_unfixed_violations_after_the_limit_return_the_best_draft() -> None:
    worse = f"{WITH_FILLER} {UNSUPPORTED}"
    writer = ScriptedLLMClient(single_reply(worse), single_reply(WITH_FILLER))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler")),
        findings(finding(FILLER, "filler"), finding(UNSUPPORTED, "unsupported_claim")),
        findings(finding(FILLER, "filler")),
    )

    result = await review(writer, critic, WITH_FILLER)

    assert len(writer.calls) == 2
    assert len(critic.calls) == 3
    assert result.attempts == 3
    assert result.regenerations == 2
    assert result.chosen_attempt == 3
    assert [violation.rule for violation in result.report.violations] == [StyleRule.FILLER]
    assert not result.report.passed


async def test_the_best_draft_is_an_earlier_one_when_later_ones_are_worse() -> None:
    writer = ScriptedLLMClient(single_reply(WITH_UNSUPPORTED), single_reply(WITH_UNSUPPORTED))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler")),
        findings(finding(UNSUPPORTED, "unsupported_claim")),
        findings(finding(UNSUPPORTED, "unsupported_claim")),
    )

    result = await review(writer, critic, WITH_FILLER)

    assert result.chosen_attempt == 1
    assert result.draft.texts == [WITH_FILLER]


async def test_one_cliche_beats_one_unsupported_claim_with_fewer_violations_in_total() -> None:
    two_style = f"{CLEAN} {CLICHE} Победа — начало."
    writer = ScriptedLLMClient(single_reply(two_style))
    critic = ScriptedLLMClient(
        findings(finding(UNSUPPORTED, "unsupported_claim")),
        findings(finding(CLICHE, "cliche")),
    )

    result = await review(
        writer, critic, WITH_UNSUPPORTED, limits=make_style_limits(max_regenerations=1)
    )

    assert result.chosen_attempt == 2
    assert [violation.rule for violation in result.report.violations] == [
        StyleRule.DASH,
        StyleRule.CLICHE,
    ]


async def test_on_a_tie_the_last_draft_wins() -> None:
    writer = ScriptedLLMClient(single_reply(f"{CLEAN} {CLICHE}"))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler")),
        findings(finding(CLICHE, "cliche")),
    )

    result = await review(
        writer, critic, WITH_FILLER, limits=make_style_limits(max_regenerations=1)
    )

    assert result.chosen_attempt == 2


async def test_the_dangerous_rules_are_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(style, "DANGEROUS_STYLE_RULES", frozenset({"cliche"}))
    writer = ScriptedLLMClient(single_reply(f"{CLEAN} {CLICHE}"))
    critic = ScriptedLLMClient(
        findings(finding(UNSUPPORTED, "unsupported_claim")),
        findings(finding(CLICHE, "cliche")),
    )

    result = await review(
        writer, critic, WITH_UNSUPPORTED, limits=make_style_limits(max_regenerations=1)
    )

    assert result.chosen_attempt == 1


def test_every_dangerous_rule_is_a_known_rule() -> None:
    assert {StyleRule(rule) for rule in style.DANGEROUS_STYLE_RULES} == {
        StyleRule.UNSUPPORTED_CLAIM,
        StyleRule.UNVERIFIED_NUMBER,
        StyleRule.AMBIGUOUS_REFERENCE,
        StyleRule.INVENTED_EXPERIENCE,
    }


async def test_unverified_numbers_trigger_a_regeneration() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(NO_FINDINGS, NO_FINDINGS)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([f"{CLEAN} Через 2 года Москва сгорела."], unverified_numbers=["2"]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert len(writer.calls) == 1
    assert "«2»: Числа или доли «2» нет ни в одном факте." in revision_prompt(writer, 0)
    assert result.report.passed
    assert result.draft.unverified_numbers == []


async def test_a_share_set_is_revised_as_one_picture() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(NO_FINDINGS, NO_FINDINGS)
    picture = "Около трети были за князя, шестая часть против, около половины молчали."
    forms = ["трети", "шестая часть", "половины"]

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([f"{CLEAN} {picture}"], unverified_numbers=forms, unverified_share_sets=[forms]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    prompt = revision_prompt(writer, 0)
    assert "- Доли «трети», «шестая часть», «половины» вместе складываются в целое" in prompt
    assert "Убери всю эту картину целого, а не одну долю" in prompt
    assert "Числа или доли «трети»" not in prompt
    assert result.draft.unverified_share_sets == []
    assert result.report.passed


async def test_a_length_violation_alone_does_not_trigger_a_regeneration() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(NO_FINDINGS)
    length = LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=300, limit=280)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([CLEAN], length_violations=[length]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert writer.calls == []
    assert [violation.rule for violation in result.report.violations] == [StyleRule.LENGTH]


async def test_a_length_violation_is_in_the_instruction_when_regenerating() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")), NO_FINDINGS)
    length = LengthViolation(issue=LengthIssue.PART_TOO_LONG, part=1, actual=300, limit=280)

    await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([WITH_FILLER], length_violations=[length]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert "- Часть 1: 300 знаков при пределе 280." in revision_prompt(writer, 0)


async def test_a_regenerated_short_post_goes_through_the_length_retries() -> None:
    long_text = "Слово " * 60
    writer = ScriptedLLMClient(single_reply(long_text.strip()), single_reply(CLEAN))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")), NO_FINDINGS)

    result = await review(writer, critic, WITH_FILLER)

    assert len(writer.calls) == 2
    assert result.draft.texts == [CLEAN]
    assert result.draft.attempts == 2
    assert result.draft.length_violations == []


async def test_a_thread_is_regenerated_as_a_thread() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, "Мамай ждал Ягайло."))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")), NO_FINDINGS)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([CLEAN, f"Мамай ждал Ягайло. {FILLER}"]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    assert writer.calls[0][1] is ThreadReply
    assert f"- part 2: «{FILLER}»" in revision_prompt(writer, 0)
    assert result.draft.post_format is PostFormat.THREAD
    assert result.report.passed


async def test_a_closing_question_regenerates_unless_allowed() -> None:
    question = f"{CLEAN} А вы бы ждали?"
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(NO_FINDINGS, NO_FINDINGS)

    result = await review(writer, critic, question)

    assert len(writer.calls) == 1
    assert result.report.passed

    allowed = await review(
        ScriptedLLMClient(), ScriptedLLMClient(NO_FINDINGS), question, allow_closing_question=True
    )
    assert allowed.report.passed


@pytest.mark.parametrize(
    "error", [LLMUnavailableError("down"), LLMInvalidResponseError("invalid json reply")]
)
async def test_a_critic_error_keeps_the_draft_and_the_code_findings(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(error)

    with caplog.at_level(logging.WARNING):
        result = await review(writer, critic, CLEAN)

    assert writer.calls == []
    assert result.report.critic is CriticStatus.FAILED
    assert result.report.violations == []
    assert result.draft.texts == [CLEAN]
    assert type(error).__name__ in caplog.text


async def test_an_invalid_critic_reply_gives_critic_failed() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient({"findings": "нет"})

    result = await review(writer, critic, CLEAN)

    assert result.report.critic is CriticStatus.FAILED


async def test_code_violations_still_regenerate_when_the_critic_fails() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(LLMUnavailableError("down"), NO_FINDINGS)

    result = await review(writer, critic, WITH_DASH)

    assert len(writer.calls) == 1
    assert result.report.passed
    assert result.report.critic is CriticStatus.CHECKED


async def test_a_disabled_critic_is_never_called() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient()

    result = await review(writer, critic, WITH_DASH, limits=make_style_limits(critic_enabled=False))

    assert critic.calls == []
    assert len(writer.calls) == 1
    assert result.report.critic is CriticStatus.DISABLED
    assert result.report.passed


async def test_no_regeneration_when_the_limit_is_zero() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(NO_FINDINGS)

    result = await review(writer, critic, WITH_DASH, limits=make_style_limits(max_regenerations=0))

    assert writer.calls == []
    assert [violation.rule for violation in result.report.violations] == [StyleRule.DASH]


async def test_a_regeneration_error_keeps_the_ready_draft(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = ScriptedLLMClient(LLMUnavailableError("down"))
    critic = ScriptedLLMClient(findings(finding(FILLER, "filler")))

    with caplog.at_level(logging.WARNING):
        result = await review(writer, critic, WITH_FILLER)

    assert result.regeneration_failed
    assert result.draft.texts == [WITH_FILLER]
    assert [violation.rule for violation in result.report.violations] == [StyleRule.FILLER]
    assert result.regenerations == 0
    assert "LLMUnavailableError" in caplog.text


async def test_a_second_regeneration_error_keeps_the_best_of_the_ready_drafts() -> None:
    writer = ScriptedLLMClient(single_reply(WITH_DASH), LLMInvalidResponseError("invalid"))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler"), finding(CLEAN, "cliche")), NO_FINDINGS
    )

    result = await review(writer, critic, WITH_FILLER)

    assert result.regeneration_failed
    assert result.regenerations == 1
    assert result.chosen_attempt == 2
    assert result.draft.texts == [WITH_DASH]


async def test_the_log_holds_counters_and_rule_names_but_no_texts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = ScriptedLLMClient(single_reply(WITH_UNSUPPORTED))
    critic = ScriptedLLMClient(
        findings(finding(FILLER, "filler", "Секретное пояснение.")),
        findings(finding(UNSUPPORTED, "unsupported_claim", "Секретное пояснение.")),
    )

    with caplog.at_level(logging.DEBUG):
        await review(writer, critic, WITH_FILLER, limits=make_style_limits(max_regenerations=1))

    assert "style reviewed" in caplog.text
    assert "chosen=1" in caplog.text
    assert "dangerous=0 rules=filler=1" in caplog.text
    for secret in (CLEAN, FILLER, UNSUPPORTED, "Секретное пояснение"):
        assert secret not in caplog.text


def test_style_limits_come_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="t",
        owner_telegram_ids=[1],
        style_critic_enabled=False,
        style_max_regenerations=1,
        style_critic_max_findings=5,
    )

    assert StyleLimits.from_settings(settings) == StyleLimits(
        critic_enabled=False, max_regenerations=1, critic_max_findings=5
    )
