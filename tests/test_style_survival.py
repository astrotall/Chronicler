import logging

import pytest
from app.domain.draft import PostFormat
from app.domain.fact import ClaimStance, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Message
from app.domain.style import StyleResult, StyleRule
from app.services.generator import WritingLimits
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
SECOND = "Второй твит о другом."
THIRD = "Третий твит о войске."
FLAGGED = "Тот же график привёл к росту брака на заводах и падению выпуска продукции."
REWORDED = "Тот же график вёл к росту брака на заводах и падению выпуска продукции."
PARAPHRASE = "Завод работал по новому графику."
EXPLANATION = "Причина, которой нет в фактах."
DELETE_HEADER = "Flagged sentences. Delete the flagged sentence."
NO_REWORDING = "Do not reword, restate or soften what you delete"
RESTATE_ALTERNATIVE = "restate it exactly as the fact says"
CLARIFY_HEADER = "Unclear references. Name the subject explicitly"
STILL_HEADER = "Still in the text after the last attempt. Delete these sentences"
PROBLEMS_HEADER = "Problems:\n"
FLAGGED_FINDING = findings(finding(FLAGGED, "unsupported_claim", EXPLANATION))
REWORDED_FINDING = findings(finding(REWORDED, "unsupported_claim", EXPLANATION))


def prompt_of(writer: ScriptedLLMClient, index: int) -> str:
    messages: list[Message] = writer.calls[index][0]
    return messages[-1].content


async def review_thread_of(
    writer: ScriptedLLMClient,
    critic: ScriptedLLMClient,
    tweets: list[str],
    *,
    limits: StyleLimits | None = None,
    writing_limits: WritingLimits | None = None,
    fact_set: FactSet = FACT_SET,
    used: tuple[str, ...] = ("F1",),
) -> StyleResult:
    return await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(tweets, PostFormat.THREAD, used_fact_ids=used),
        fact_set,
        writing_limits or make_writing_limits(),
        limits or make_style_limits(),
    )


async def test_the_instruction_demands_deletion_and_forbids_rewording() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    prompt = prompt_of(writer, 0)
    assert DELETE_HEADER in prompt
    assert NO_REWORDING in prompt
    assert RESTATE_ALTERNATIVE not in prompt
    assert CLARIFY_HEADER not in prompt
    assert f"- part 2: «{FLAGGED}»: {EXPLANATION}" in prompt
    assert "Fix only the flagged fragments below." in prompt
    assert STILL_HEADER not in prompt


@pytest.mark.parametrize("rule", ["unsupported_claim", "filler", "cliche"])
async def test_every_deletable_rule_gets_the_deletion_wording(rule: str) -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(findings(finding(FLAGGED, rule)), NO_FINDINGS)

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert DELETE_HEADER in prompt_of(writer, 0)


async def test_an_ambiguous_reference_is_told_to_name_the_subject_not_to_delete() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(
        findings(finding(FLAGGED, "ambiguous_reference", "Неясно, кто сделал.")), NO_FINDINGS
    )

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    prompt = prompt_of(writer, 0)
    assert CLARIFY_HEADER in prompt
    assert "Do not delete the sentence" in prompt
    assert f"- part 2: «{FLAGGED}»: Неясно, кто сделал." in prompt
    assert DELETE_HEADER not in prompt
    assert "Fix only the flagged fragments below." in prompt


async def test_an_ambiguous_reference_is_never_a_survivor() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, FLAGGED))
    critic = ScriptedLLMClient(
        findings(finding(FLAGGED, "ambiguous_reference", "Неясно, кто сделал.")), NO_FINDINGS
    )

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert result.unfixed_per_round == [0]
    assert len(writer.calls) == 1


@pytest.mark.parametrize("rule", ["opinion", "triplet"])
async def test_other_critic_rules_keep_the_plain_wording(rule: str) -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(findings(finding(FLAGGED, rule)), NO_FINDINGS)

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    prompt = prompt_of(writer, 0)
    assert DELETE_HEADER not in prompt
    assert PROBLEMS_HEADER in prompt


async def test_a_dash_stays_in_the_plain_problems_list() -> None:
    writer = ScriptedLLMClient(single_reply(CLEAN))
    critic = ScriptedLLMClient(NO_FINDINGS, NO_FINDINGS)

    await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(["8 сентября 1380 года — на Куликовом поле сошлись войска."]),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(),
    )

    prompt = prompt_of(writer, 0)
    assert PROBLEMS_HEADER in prompt
    assert DELETE_HEADER not in prompt


async def test_a_kept_phrase_is_named_again_with_a_demand_to_delete_it() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED), thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, REWORDED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    second = prompt_of(writer, 1)
    assert STILL_HEADER in second
    assert f"- part 2: «{REWORDED}»: {EXPLANATION}" in second
    assert result.report.passed
    assert result.draft.texts == [CLEAN, SECOND]
    assert result.unfixed_per_round == [1, 0]


async def test_a_deletion_is_accepted_and_nothing_is_named_again() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert len(writer.calls) == 1
    assert result.unfixed_per_round == [0]
    assert result.report.passed
    assert result.regressions_rejected == 0


async def test_a_reworded_copy_is_detected_by_the_overlap_and_a_paraphrase_is_not() -> None:
    reworded = await review_thread_of(
        ScriptedLLMClient(thread_reply(CLEAN, REWORDED)),
        ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS),
        [CLEAN, FLAGGED],
    )
    paraphrased = await review_thread_of(
        ScriptedLLMClient(thread_reply(CLEAN, PARAPHRASE)),
        ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS),
        [CLEAN, FLAGGED],
    )

    assert reworded.unfixed_per_round == [1]
    assert paraphrased.unfixed_per_round == [0]


async def test_the_overlap_threshold_comes_from_the_limits() -> None:
    result = await review_thread_of(
        ScriptedLLMClient(thread_reply(CLEAN, REWORDED)),
        ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS),
        [CLEAN, FLAGGED],
        limits=make_style_limits(fragment_overlap=0.95),
    )

    assert result.unfixed_per_round == [0]


async def test_a_fragment_moved_to_another_tweet_is_still_detected() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND, REWORDED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED, THIRD])

    assert result.unfixed_per_round == [1]


async def test_a_verbatim_survivor_keeps_the_loop_going_when_the_critic_is_silent() -> None:
    writer = ScriptedLLMClient(
        thread_reply(CLEAN, SECOND, FLAGGED), thread_reply(CLEAN, SECOND, THIRD)
    )
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED, THIRD])

    assert len(writer.calls) == 2
    assert len(critic.calls) == 3
    second = prompt_of(writer, 1)
    assert STILL_HEADER in second
    assert f"- part 3: «{FLAGGED}»: {EXPLANATION}" in second
    assert result.unfixed_per_round == [1, 0]
    assert result.draft.texts == [CLEAN, SECOND, THIRD]


async def test_a_near_copy_alone_does_not_trigger_another_round() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert len(writer.calls) == 1
    assert len(critic.calls) == 2
    assert result.unfixed_per_round == [1]
    assert result.regenerations == 1
    assert result.draft.texts == [CLEAN, REWORDED]


async def test_a_near_copy_is_named_when_another_regeneration_happens_anyway() -> None:
    writer = ScriptedLLMClient(
        thread_reply(CLEAN, REWORDED), thread_reply("Другое начало поста.", SECOND)
    )
    critic = ScriptedLLMClient(
        FLAGGED_FINDING, findings(finding(CLEAN, "cliche", "Штамп.")), NO_FINDINGS
    )

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    second = prompt_of(writer, 1)
    assert STILL_HEADER in second
    assert f"«{REWORDED}»" in second
    assert f"- part 1: «{CLEAN}»: Штамп." in second
    assert result.unfixed_per_round == [1, 0]


async def test_a_survivor_the_critic_still_flags_is_listed_once() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, FLAGGED), thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, FLAGGED_FINDING, NO_FINDINGS)

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert prompt_of(writer, 1).count(f"- part 2: «{FLAGGED}»") == 1


async def test_the_survivor_is_forbidden_wording_in_later_attempts() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED), thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, REWORDED_FINDING, NO_FINDINGS)

    await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert f"«{FLAGGED}», «{REWORDED}»" in prompt_of(writer, 1)


LEGEND = "По преданию, перед битвой Пересвет бился с Челубеем, и оба пали на поле."
PLAIN = "Перед битвой Пересвет бился с Челубеем, и оба пали на поле."


def fact(fact_id: str, text: str, stance: ClaimStance = ClaimStance.ASSERTED) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="s", url="https://example.org", domain="example.org", quote=text)
        ],
        status=FactStatus.SINGLE,
        stance=stance,
    )


CLAIMED_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        fact("F1", "Куликовская битва произошла 8 сентября 1380 года."),
        fact("F2", LEGEND, ClaimStance.CLAIMED),
    ],
    disputes=[],
)
ASSERTED_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        fact("F1", "Куликовская битва произошла 8 сентября 1380 года."),
        fact("F2", PLAIN),
        fact(
            "F3",
            "По слухам, Мамай ждал союзника у реки Оки с большим войском.",
            ClaimStance.CLAIMED,
        ),
    ],
    disputes=[],
)


async def test_the_attribution_of_a_claimed_fact_is_not_demanded_for_deletion() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, LEGEND))
    critic = ScriptedLLMClient(
        findings(finding(LEGEND, "unsupported_claim", "Утверждение без опоры.")), NO_FINDINGS
    )

    result = await review_thread_of(writer, critic, [CLEAN, LEGEND], fact_set=CLAIMED_SET)

    prompt = prompt_of(writer, 0)
    assert DELETE_HEADER not in prompt
    assert f"{PROBLEMS_HEADER}- part 2: «{LEGEND}»: Утверждение без опоры." in prompt
    assert result.unfixed_per_round == [0]
    assert result.attribution_kept == 1


async def test_the_same_words_on_an_asserted_fact_are_demanded_for_deletion() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, LEGEND), thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(
        findings(finding(LEGEND, "unsupported_claim", "Добавленная оговорка.")),
        NO_FINDINGS,
        NO_FINDINGS,
    )

    result = await review_thread_of(writer, critic, [CLEAN, LEGEND], fact_set=ASSERTED_SET)

    prompt = prompt_of(writer, 0)
    assert DELETE_HEADER in prompt
    assert "- part 2: «" + LEGEND + "»: Добавленная оговорка." in prompt
    assert result.unfixed_per_round == [1, 0]
    assert result.attribution_kept == 0


STUBBORN = [thread_reply(CLEAN, SECOND, FLAGGED), thread_reply(CLEAN, SECOND, FLAGGED)]


def stubborn_critic(*extra: dict[str, object]) -> ScriptedLLMClient:
    return ScriptedLLMClient(FLAGGED_FINDING, FLAGGED_FINDING, FLAGGED_FINDING, *extra)


async def run_stubborn(
    limits: StyleLimits,
    *,
    tweets: list[str] | None = None,
    writing_limits: WritingLimits | None = None,
    post_format: PostFormat = PostFormat.THREAD,
    replies: list[dict[str, object]] | None = None,
    fact_set: FactSet = FACT_SET,
    facts: tuple[str, ...] = ("F1",),
) -> tuple[StyleResult, ScriptedLLMClient]:
    start = tweets or [CLEAN, SECOND, FLAGGED]
    same: dict[str, object] = {"fact_ids": list(facts), "tweets": start}
    writer = ScriptedLLMClient(*(replies or [same, same]))
    critic = stubborn_critic()
    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(start, post_format, used_fact_ids=facts),
        fact_set,
        writing_limits or make_writing_limits(),
        limits,
    )
    return result, writer


async def test_the_last_resort_is_off_by_default() -> None:
    result, _ = await run_stubborn(make_style_limits())

    assert result.draft.texts == [CLEAN, SECOND, FLAGGED]
    assert result.draft.removed_fragments == []
    assert [violation.rule for violation in result.report.violations] == [
        StyleRule.UNSUPPORTED_CLAIM
    ]


async def test_the_last_resort_removes_the_flagged_sentence_when_on() -> None:
    result, _ = await run_stubborn(make_style_limits(drop_surviving_claims=True))

    assert result.draft.texts == [CLEAN, SECOND]
    assert result.draft.removed_fragments == [FLAGGED]
    assert result.report.passed
    assert result.unfixed_per_round == [1, 1]


async def test_the_last_resort_removes_only_the_flagged_sentence_not_its_neighbours() -> None:
    tweets = [CLEAN, f"{SECOND} {FLAGGED} Конец твита."]
    replies = [thread_reply(*tweets), thread_reply(*tweets)]

    result, _ = await run_stubborn(
        make_style_limits(drop_surviving_claims=True), tweets=tweets, replies=replies
    )

    assert result.draft.texts == [CLEAN, f"{SECOND} Конец твита."]
    assert result.draft.removed_fragments == [FLAGGED]


async def test_the_last_resort_never_cuts_inside_a_sentence() -> None:
    tweets = [CLEAN, f"{SECOND} {FLAGGED}"]
    partial = findings(finding("привёл к росту брака", "unsupported_claim", EXPLANATION))
    writer = ScriptedLLMClient(thread_reply(*tweets), thread_reply(*tweets))
    critic = ScriptedLLMClient(partial, partial, partial)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(tweets, PostFormat.THREAD),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(drop_surviving_claims=True),
    )

    assert result.draft.texts == tweets
    assert result.draft.removed_fragments == []
    assert len(result.report.violations) == 1


async def test_the_last_resort_keeps_the_thread_minimum() -> None:
    limits = WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        thread_min_tweets=3,
        thread_min_used_facts=3,
    )

    result, _ = await run_stubborn(
        make_style_limits(drop_surviving_claims=True),
        writing_limits=limits,
        facts=("F1", "F2", "F3"),
    )

    assert len(result.draft.parts) == 3
    assert result.draft.removed_fragments == []
    assert len(result.report.violations) == 1


async def test_the_last_resort_removes_a_whole_tweet_above_the_minimum() -> None:
    limits = WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        thread_min_tweets=2,
        thread_min_used_facts=2,
    )

    result, _ = await run_stubborn(
        make_style_limits(drop_surviving_claims=True),
        writing_limits=limits,
        facts=("F1", "F2"),
    )

    assert len(result.draft.parts) == 2
    assert result.draft.removed_fragments == [FLAGGED]


async def test_the_last_resort_never_applies_to_a_short_post() -> None:
    text = f"{CLEAN} {FLAGGED}"
    writer = ScriptedLLMClient(single_reply(text), single_reply(text))
    critic = stubborn_critic()

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft([text], PostFormat.SHORT),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(drop_surviving_claims=True),
    )

    assert result.draft.texts == [text]
    assert result.draft.removed_fragments == []
    assert len(result.report.violations) == 1


LONG_BODY = "а" * 300 + "."


BIG_FLAGGED = (
    "Тот же график привёл к росту брака на заводах и падению выпуска продукции и к остановке "
    "большинства цехов в течение нескольких недель подряд на всех крупных предприятиях области."
)


async def run_long(
    limits: StyleLimits,
    *,
    body: str = LONG_BODY,
    long_min_chars: int = 0,
    flagged: str = FLAGGED,
) -> StyleResult:
    text = f"{body} {flagged}"
    writer = ScriptedLLMClient(single_reply(text), single_reply(text))
    claim = findings(finding(flagged, "unsupported_claim", EXPLANATION))
    return await review_style(
        as_client(writer),
        as_client(ScriptedLLMClient(claim, claim, claim)),
        make_draft([text], PostFormat.LONG),
        FACT_SET,
        WritingLimits(
            short_max_chars=280,
            long_max_chars=25000,
            thread_tweet_max_chars=280,
            thread_max_tweets=12,
            long_min_chars=long_min_chars,
        ),
        limits,
    )


async def test_the_last_resort_cuts_a_long_post_and_keeps_its_paragraphs() -> None:
    text = f"{LONG_BODY}\n\n{CLEAN} {FLAGGED}\n\nКонец."
    writer = ScriptedLLMClient(single_reply(text), single_reply(text))

    result = await review_style(
        as_client(writer),
        as_client(stubborn_critic()),
        make_draft([text], PostFormat.LONG),
        FACT_SET,
        make_writing_limits(),
        make_style_limits(drop_surviving_claims=True),
    )

    assert result.draft.texts == [f"{LONG_BODY}\n\n{CLEAN}\n\nКонец."]
    assert result.draft.removed_fragments == [FLAGGED]


async def test_the_last_resort_keeps_the_long_character_minimum() -> None:
    cut = await run_long(make_style_limits(drop_surviving_claims=True), long_min_chars=0)
    blocked = await run_long(make_style_limits(drop_surviving_claims=True), long_min_chars=350)

    assert cut.draft.removed_fragments == [FLAGGED]
    assert blocked.draft.removed_fragments == []


async def test_the_last_resort_respects_the_regression_tolerance() -> None:
    limits = make_style_limits(drop_surviving_claims=True)
    cut = await run_long(limits, body="б" * 600 + ".", flagged=BIG_FLAGGED)
    blocked = await run_long(limits, body="в" * 120 + ".", flagged=BIG_FLAGGED)

    assert cut.draft.removed_fragments == [BIG_FLAGGED]
    assert blocked.draft.removed_fragments == []
    assert len(blocked.report.violations) == 1


async def test_the_last_resort_never_removes_an_attribution() -> None:
    tweets = [CLEAN, SECOND, LEGEND]
    flagged = findings(finding(LEGEND, "unsupported_claim", "Утверждение без опоры."))
    writer = ScriptedLLMClient(thread_reply(*tweets), thread_reply(*tweets))
    critic = ScriptedLLMClient(flagged, flagged, flagged)

    result = await review_style(
        as_client(writer),
        as_client(critic),
        make_draft(tweets, PostFormat.THREAD),
        CLAIMED_SET,
        make_writing_limits(),
        make_style_limits(drop_surviving_claims=True),
    )

    assert result.draft.texts == tweets
    assert result.draft.removed_fragments == []


async def test_the_last_resort_never_removes_a_sentence_found_only_by_the_overlap_check() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(
        writer, critic, [CLEAN, FLAGGED], limits=make_style_limits(drop_surviving_claims=True)
    )

    assert result.draft.texts == [CLEAN, REWORDED]
    assert result.draft.removed_fragments == []


@pytest.mark.parametrize("rule", ["unsupported_claim", "filler", "cliche"])
async def test_the_last_resort_removes_a_flagged_sentence_that_is_not_a_survivor(rule: str) -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND, THIRD))
    critic = ScriptedLLMClient(FLAGGED_FINDING, findings(finding(THIRD, rule, EXPLANATION)))

    result = await review_thread_of(
        writer,
        critic,
        [CLEAN, FLAGGED, SECOND],
        limits=make_style_limits(max_regenerations=1, drop_surviving_claims=True),
    )

    assert result.draft.texts == [CLEAN, SECOND]
    assert result.draft.removed_fragments == [THIRD]
    assert result.unfixed_per_round == [0]
    assert result.report.passed


async def test_the_last_resort_does_not_touch_other_critic_rules() -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(
        findings(
            finding(THIRD, "opinion", EXPLANATION),
            finding(SECOND, "ambiguous_reference", EXPLANATION),
        )
    )

    result = await review_thread_of(
        writer,
        critic,
        [CLEAN, SECOND, THIRD],
        limits=make_style_limits(max_regenerations=0, drop_surviving_claims=True),
    )

    assert result.draft.texts == [CLEAN, SECOND, THIRD]
    assert result.draft.removed_fragments == []


async def test_the_last_resort_does_nothing_when_nothing_is_flagged_in_the_chosen_version() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(
        writer, critic, [CLEAN, FLAGGED], limits=make_style_limits(drop_surviving_claims=True)
    )

    assert result.draft.texts == [CLEAN, SECOND]
    assert result.draft.removed_fragments == []


async def test_a_flagged_sentence_that_is_not_a_survivor_stays_while_the_step_is_off() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, SECOND, THIRD))
    critic = ScriptedLLMClient(
        FLAGGED_FINDING, findings(finding(THIRD, "unsupported_claim", EXPLANATION))
    )

    result = await review_thread_of(
        writer, critic, [CLEAN, FLAGGED, SECOND], limits=make_style_limits(max_regenerations=1)
    )

    assert result.draft.texts == [CLEAN, SECOND, THIRD]
    assert result.draft.removed_fragments == []
    assert not result.report.passed


async def test_the_last_resort_refuses_a_sentence_followed_by_a_dangling_reference() -> None:
    follower = "Это решило исход битвы."
    tweets = [CLEAN, f"{FLAGGED} {follower}"]
    writer = ScriptedLLMClient(thread_reply(*tweets))
    critic = ScriptedLLMClient(FLAGGED_FINDING, FLAGGED_FINDING)

    result = await review_thread_of(
        writer,
        critic,
        [CLEAN, f"{SECOND} {FLAGGED}"],
        limits=make_style_limits(max_regenerations=1, drop_surviving_claims=True),
    )

    assert result.draft.texts == tweets
    assert result.draft.removed_fragments == []
    assert result.removal_blocked == 1


async def test_the_last_resort_removes_a_sentence_followed_by_a_plain_one() -> None:
    tweets = [CLEAN, f"{FLAGGED} Затем начался бой."]
    writer = ScriptedLLMClient(thread_reply(*tweets))
    critic = ScriptedLLMClient(FLAGGED_FINDING, FLAGGED_FINDING)

    result = await review_thread_of(
        writer,
        critic,
        [CLEAN, f"{SECOND} {FLAGGED}"],
        limits=make_style_limits(max_regenerations=1, drop_surviving_claims=True),
    )

    assert result.draft.texts == [CLEAN, "Затем начался бой."]
    assert result.removal_blocked == 0


async def test_verbatim_survivors_the_last_critic_pass_no_longer_flags_are_counted() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, FLAGGED), thread_reply(CLEAN, FLAGGED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert result.draft.texts == [CLEAN, FLAGGED]
    assert result.report.passed
    assert result.unreported_survivors == 1


async def test_a_survivor_the_critic_still_flags_is_not_unreported() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, FLAGGED), thread_reply(CLEAN, FLAGGED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, FLAGGED_FINDING, FLAGGED_FINDING)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert result.unreported_survivors == 0
    assert not result.report.passed


async def test_a_deleted_survivor_is_not_unreported() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, FLAGGED), thread_reply(CLEAN, SECOND))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert result.unreported_survivors == 0


async def test_a_near_copy_the_critic_no_longer_flags_is_not_counted_as_unreported() -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, [CLEAN, FLAGGED])

    assert result.unreported_survivors == 0


NUMBERED = "Карточную систему отменят уже в 1935 году по всей стране."


async def test_a_removal_refreshes_the_numbers_and_the_report() -> None:
    claim = findings(finding(NUMBERED, "unsupported_claim", EXPLANATION))
    tweets = [CLEAN, SECOND, NUMBERED]
    results = []
    for drop in (False, True):
        writer = ScriptedLLMClient(thread_reply(*tweets), thread_reply(*tweets))
        results.append(
            await review_style(
                as_client(writer),
                as_client(ScriptedLLMClient(claim, claim, claim)),
                make_draft(tweets, PostFormat.THREAD, unverified_numbers=["1935"]),
                FACT_SET,
                make_writing_limits(),
                make_style_limits(drop_surviving_claims=drop),
            )
        )
    kept, cut = results

    assert kept.draft.unverified_numbers == ["1935"]
    assert not kept.report.passed
    assert cut.draft.removed_fragments == [NUMBERED]
    assert cut.draft.unverified_numbers == []
    assert cut.report.passed


def style_line(caplog: pytest.LogCaptureFixture) -> str:
    lines = [record.getMessage() for record in caplog.records if "style reviewed" in record.message]
    assert len(lines) == 1
    return lines[0]


async def test_the_style_line_has_counters_only(caplog: pytest.LogCaptureFixture) -> None:
    writer = ScriptedLLMClient(thread_reply(CLEAN, REWORDED), thread_reply(CLEAN, SECOND, FLAGGED))
    critic = ScriptedLLMClient(FLAGGED_FINDING, REWORDED_FINDING, FLAGGED_FINDING)

    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await review_thread_of(
            writer, critic, [CLEAN, FLAGGED], limits=make_style_limits(drop_surviving_claims=True)
        )

    line = style_line(caplog)
    assert "unfixed_per_round=0+1|0+1" in line
    assert "attribution_kept=0" in line
    assert "removed_fragments=" in line
    assert "removal_blocked=0" in line
    assert "unreported_survivors=" in line
    assert FLAGGED not in line
    assert REWORDED not in line


async def test_the_style_line_logs_none_without_regeneration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = ScriptedLLMClient()
    critic = ScriptedLLMClient(NO_FINDINGS)

    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await review_thread_of(writer, critic, [CLEAN, SECOND])

    line = style_line(caplog)
    assert "unfixed_per_round=none" in line
    assert "removed_fragments=0" in line


async def test_the_log_counts_removed_fragments_and_never_prints_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.style_review"):
        await run_stubborn(make_style_limits(drop_surviving_claims=True))

    line = style_line(caplog)
    assert "removed_fragments=1" in line
    assert FLAGGED not in line


async def test_a_deletion_of_one_sentence_is_not_a_regression() -> None:
    plain = ["Б" + "а" * 200 + ".", "В" + "а" * 200 + "."]
    long_tweets = [f"А{'а' * 200}. {FLAGGED}", *plain]
    shorter = [f"А{'а' * 200}.", *plain]
    writer = ScriptedLLMClient(thread_reply(*shorter))
    critic = ScriptedLLMClient(FLAGGED_FINDING, NO_FINDINGS)

    result = await review_thread_of(writer, critic, long_tweets)

    assert result.regressions_rejected == 0
    assert result.draft.texts == shorter


async def test_deleting_a_whole_tweet_of_a_thread_at_its_minimum_is_still_a_regression() -> None:
    limits = WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        thread_min_tweets=3,
        thread_min_used_facts=3,
    )
    two = {"fact_ids": ["F1", "F2", "F3"], "tweets": [CLEAN, SECOND]}
    writer = ScriptedLLMClient(two, two)
    critic = ScriptedLLMClient(FLAGGED_FINDING)

    result = await review_thread_of(
        writer,
        critic,
        [CLEAN, SECOND, FLAGGED],
        writing_limits=limits,
        limits=make_style_limits(max_regenerations=1),
        used=("F1", "F2", "F3"),
    )

    assert result.regressions_rejected == 1
    assert result.draft.texts == [CLEAN, SECOND, FLAGGED]
