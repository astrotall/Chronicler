import json
from pathlib import Path

from app.bot import messages
from app.bot.formatting import facts_messages, render_topic_outcome, warnings
from app.domain.draft import Draft, DraftPart, PostFormat, SentenceBudget
from app.domain.fact import (
    ClaimStance,
    Dispute,
    ExtractionStats,
    Fact,
    FactSet,
    FactStatus,
    InsufficientFacts,
    SourceRef,
)
from app.domain.llm import Message, Role
from app.domain.pipeline import NotEnoughFacts, PostAction, PostReady
from app.domain.style import CriticStatus, StyleReport, StyleResult, StyleRule
from app.prompts.style_critique import render_critique
from app.prompts.writing import assertable_facts, attributed_ids, cautious_ids, render_writing
from app.services.disputes import with_disputed_facts
from app.services.generator import WritingLimits, long_size, unused_fact_ids
from app.services.pipeline import assertable_count
from app.services.short_post import select_short_facts
from app.services.style_critic import critique_draft

from fact_snapshot import FACT_SET_KEY, fact_set_of, load_fact_set, snapshot_of
from llm_helpers import ScriptedLLMClient, as_client
from style_helpers import finding, findings

DATE = "Куликовская битва произошла 8 сентября 1380 года."
PLACE = "Сражение произошло на Куликовом поле."
DUEL = "По преданию, перед битвой инок Пересвет бился с богатырём Челубеем."
MYTH = "По распространённому утверждению, в календаре 1930 года было 30 февраля."
REBUTTAL = "Сохранившиеся табели 1930 года показывают обычный февраль из 28 дней."
ARMY_60 = "Русское войско насчитывало 60 000 человек."
ARMY_150 = "Русское войско насчитывало 150 000 человек."
SOURCE = SourceRef(
    snippet_id="s", url="https://history.example.org/a", domain="example.org", quote="цитата"
)
BUDGET = SentenceBudget(max_sentences=3, sentence_chars=80)


def stance_fact(
    fact_id: str,
    text: str,
    stance: ClaimStance = ClaimStance.ASSERTED,
    *,
    status: FactStatus = FactStatus.SINGLE,
    rebutted_by: tuple[str, ...] = (),
) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[SOURCE],
        status=status,
        stance=stance,
        rebutted_by=list(rebutted_by),
    )


FACT_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        stance_fact("F1", DATE, status=FactStatus.CONFIRMED),
        stance_fact("F2", PLACE),
        stance_fact("F3", REBUTTAL),
        stance_fact("F4", DUEL, ClaimStance.CLAIMED),
        stance_fact("F5", MYTH, ClaimStance.REBUTTED, rebutted_by=("F3",)),
        stance_fact("F6", ARMY_60, status=FactStatus.DISPUTED),
        stance_fact("F7", ARMY_150, status=FactStatus.DISPUTED),
    ],
    disputes=[Dispute(fact_ids=["F6", "F7"], explanation="Оценки расходятся.")],
)


def user_text(prompt: list[Message]) -> str:
    [user] = [message.content for message in prompt if message.role is Role.USER]
    return user


def system_text(prompt: list[Message]) -> str:
    return "\n".join(message.content for message in prompt if message.role is Role.SYSTEM)


def writing_prompt(fact_set: FactSet = FACT_SET) -> list[Message]:
    return render_writing(fact_set, PostFormat.LONG, max_chars=25000, max_tweets=12, budget=BUDGET)


def test_attributed_ids_exclude_asserted_and_disputed_facts() -> None:
    disputed_claim = FACT_SET.model_copy(
        update={
            "facts": [
                *FACT_SET.facts,
                stance_fact("F8", DUEL, ClaimStance.CLAIMED, status=FactStatus.DISPUTED),
            ]
        }
    )

    assert attributed_ids(disputed_claim) == {"F4", "F5"}
    assert cautious_ids(disputed_claim) == {"F4", "F5", "F6", "F7", "F8"}
    assert [fact.id for fact in assertable_facts(disputed_claim)] == ["F1", "F2", "F3"]


def test_the_writer_gets_attributed_claims_in_their_own_block() -> None:
    user = user_text(writing_prompt())
    facts_block, rest = user.split("Attributed claims.")
    attributed_block, disputes_block = rest.split("Disputed facts.")

    assert DUEL not in facts_block
    assert MYTH not in facts_block
    assert REBUTTAL in facts_block
    assert f"F4 [claimed]: {DUEL}" in attributed_block
    assert f"F5 [rebutted; rebuttal: F3]: {MYTH}" in attributed_block
    assert "Never state any of these as a fact" in attributed_block
    assert ARMY_60 in disputes_block


def test_a_rebutted_claim_without_its_rebuttal_in_the_set_says_so() -> None:
    alone = FactSet(
        topic="Календарь",
        facts=[
            stance_fact("F1", DATE),
            stance_fact("F2", MYTH, ClaimStance.REBUTTED, rebutted_by=("F9",)),
        ],
        disputes=[],
    )

    assert "F2 [rebutted; rebuttal: none among the facts]" in user_text(writing_prompt(alone))


def test_the_writing_rules_say_how_to_use_attributed_claims() -> None:
    system = system_text(writing_prompt())

    assert "Attributed claims are listed in their own block" in system
    assert "only with its attribution" in system
    assert "only together with its rebuttal" in system


def test_no_attributed_block_without_attributed_claims() -> None:
    plain = FactSet(topic="Битва", facts=[stance_fact("F1", DATE)], disputes=[])

    assert "Attributed claims." not in user_text(writing_prompt(plain))


def test_the_critic_sees_the_stance_of_every_attributed_claim() -> None:
    prompt = render_critique(["Текст поста."], FACT_SET, 10)
    user = user_text(prompt)
    facts_block, attributed_block = user.split("Attributed claims.")

    assert DUEL not in facts_block
    assert f"F4 [claimed]: {DUEL}" in attributed_block
    assert f"F5 [rebutted; rebuttal: F3]: {MYTH}" in attributed_block


def test_the_critic_rules_treat_honest_attribution_as_allowed_and_a_bare_claim_as_unsupported() -> (
    None
):
    system = system_text(render_critique(["Текст поста."], FACT_SET, 10))

    assert "is not among the attributed claims" in system
    assert "an attributed claim (claimed or rebutted) stated as a fact" in system
    assert "«Пересвет вышел на поединок с Челубеем» when the fact is [claimed]" in system
    assert "a rebutted claim mentioned without its rebuttal" in system
    assert "the attribution of an attributed claim" in system


async def test_a_bare_claim_is_a_violation_and_an_honest_attribution_is_withdrawn() -> None:
    texts = ["По преданию, Пересвет бился с Челубеем. Пересвет вышел на поединок первым."]
    fake = ScriptedLLMClient(
        findings(
            finding("По преданию, Пересвет бился с Челубеем", "unsupported_claim", violation=False),
            finding("Пересвет вышел на поединок первым", "unsupported_claim"),
        )
    )

    outcome = await critique_draft(as_client(fake), texts, FACT_SET, 10)

    assert [violation.excerpt for violation in outcome.violations] == [
        "Пересвет вышел на поединок первым"
    ]
    assert outcome.violations[0].rule is StyleRule.UNSUPPORTED_CLAIM
    assert outcome.withdrawn == 1
    assert f"F4 [claimed]: {DUEL}" in user_text(fake.calls[0][0])


def test_a_short_post_takes_only_asserted_facts() -> None:
    claims_first = FactSet(
        topic="Битва",
        facts=[
            stance_fact("F1", DUEL, ClaimStance.CLAIMED),
            stance_fact("F2", MYTH, ClaimStance.REBUTTED, rebutted_by=("F3",)),
            stance_fact("F3", REBUTTAL),
            stance_fact("F4", DATE, status=FactStatus.CONFIRMED),
            stance_fact("F5", PLACE),
        ],
        disputes=[],
    )

    selection = select_short_facts(claims_first, 3)

    assert [fact.id for fact in selection.facts] == ["F3", "F4", "F5"]


def test_a_short_post_never_fills_spare_slots_with_attributed_claims() -> None:
    few = FactSet(
        topic="Битва",
        facts=[
            stance_fact("F1", DATE),
            stance_fact("F2", DUEL, ClaimStance.CLAIMED),
            stance_fact("F3", MYTH, ClaimStance.REBUTTED),
        ],
        disputes=[],
    )

    assert [fact.id for fact in select_short_facts(few, 3).facts] == ["F1"]


def test_attributed_facts_are_not_assertable_for_the_thread_threshold() -> None:
    assert assertable_count(FACT_SET) == 3


def test_the_long_minimum_and_the_unused_list_ignore_attributed_facts() -> None:
    limits = WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
        long_min_chars=1200,
        long_min_used_facts=6,
    )

    assert long_size(PostFormat.LONG, limits, FACT_SET).min_facts == 3
    assert unused_fact_ids(FACT_SET, ["F1"]) == ["F2", "F3"]


def test_an_attributed_claim_whose_numbers_the_post_states_is_marked_as_used() -> None:
    used = with_disputed_facts(["F1"], ["В 1930 году якобы было 30 февраля."], FACT_SET)

    assert used == ["F1", "F5"]


def test_an_attributed_claim_without_numbers_is_marked_only_when_reported() -> None:
    assert with_disputed_facts(["F1"], ["Пересвет бился с Челубеем."], FACT_SET) == ["F1"]
    assert with_disputed_facts(["F1", "F4"], ["Пересвет бился."], FACT_SET) == ["F1", "F4"]


def make_ready(used: list[str]) -> PostReady:
    draft = Draft(
        post_format=PostFormat.SHORT,
        parts=[DraftPart(text="Текст поста.")],
        used_fact_ids=used,
        unverified_numbers=[],
        length_violations=[],
        attempts=1,
    )
    return PostReady(
        draft_id="abcdef012345",
        result=StyleResult(
            draft=draft,
            report=StyleReport(violations=[], critic=CriticStatus.CHECKED),
            attempts=1,
            chosen_attempt=1,
            regenerations=0,
        ),
        fact_set=FACT_SET,
        actions=list(PostAction),
    )


def test_the_facts_message_marks_claimed_and_rebutted_facts_and_links_the_rebuttal() -> None:
    text = "\n\n".join(facts_messages(make_ready(["F1", "F4", "F5"])))

    assert "<b>F4</b> · один источник · версия" in text
    assert "<b>F5</b> · один источник · опровергнуто" in text
    assert "Опровергается: F3" in text
    assert "Опровергает: F5" in text
    assert "<b>F1</b> · подтверждён: разные домены\n" in text


def test_a_rebutted_fact_whose_rebuttal_failed_says_so() -> None:
    alone = FACT_SET.model_copy(
        update={"facts": [stance_fact("F1", MYTH, ClaimStance.REBUTTED)], "disputes": []}
    )
    ready = make_ready(["F1"]).model_copy(update={"fact_set": alone})

    assert messages.REBUTTED_WITHOUT_REBUTTAL_LINE in "\n".join(facts_messages(ready))


def test_a_post_that_uses_attributed_claims_gets_a_warning() -> None:
    found = warnings(make_ready(["F1", "F4", "F5"]))

    assert messages.ATTRIBUTED_USED_TEMPLATE.format(ids="F4, F5") in found


def test_a_post_without_attributed_claims_gets_no_such_warning() -> None:
    found = warnings(make_ready(["F1", "F2"]))

    assert not any("версии или опровергнутые" in line for line in found)


def test_not_enough_facts_counts_attributed_claims_separately() -> None:
    outcome = NotEnoughFacts(
        extraction=InsufficientFacts(
            fact_set=FACT_SET, assertable_count=3, required=4, stats=ExtractionStats()
        ),
        disputed=2,
        attributed=2,
        failures=[],
    )

    assert render_topic_outcome(outcome).status == messages.NOT_ENOUGH_FACTS_TEMPLATE.format(
        assertable=3, disputed=2, attributed=2, required=4
    )


def test_the_stance_and_the_rebuttal_link_survive_a_snapshot_round_trip() -> None:
    loaded = fact_set_of(snapshot_of(FACT_SET))

    assert [fact.stance for fact in loaded.facts] == [fact.stance for fact in FACT_SET.facts]
    assert loaded.facts[4].rebutted_by == ["F3"]


def test_an_old_snapshot_without_a_stance_loads_as_asserted(tmp_path: Path) -> None:
    raw = snapshot_of(FACT_SET).model_dump(mode="json")
    for fact in raw["facts"]:
        del fact["stance"]
        del fact["rebutted_by"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps({FACT_SET_KEY: raw}), encoding="utf-8")

    loaded = load_fact_set(path)

    assert all(fact.stance is ClaimStance.ASSERTED for fact in loaded.facts)
    assert all(fact.rebutted_by == [] for fact in loaded.facts)
