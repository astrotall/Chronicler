import logging
from pathlib import Path

import pytest
from app.domain.draft import PostFormat
from app.domain.fact import FactStatus
from app.domain.pipeline import (
    DraftExpired,
    FailureKind,
    NoSources,
    NotEnoughFacts,
    NothingFound,
    PipelineStage,
    PostAction,
    PostReady,
    ResearchFailed,
    StepFailed,
    ThreadUnavailable,
)
from app.domain.style import CriticStatus
from app.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from app.prompts.revisions import ANGLES, REVISION_INSTRUCTIONS
from app.research.errors import SourceUnavailableError
from app.services.pipeline import Pipeline
from app.services.run_store import InMemoryRunStore

from llm_helpers import ScriptedLLMClient
from pipeline_helpers import (
    CLEAN_CRITIC,
    FULL_EXTRACTION,
    POOR_CONFLICT,
    POOR_EXTRACTION,
    SHORT_TEXT,
    SHORTER_TEXT,
    SITE,
    SNIPPETS,
    THREAD_TWEETS,
    TOPIC,
    WIKI,
    Clients,
    FakeSource,
    StageLog,
    make_limits,
    make_pipeline,
    prompt_text,
    short_reply,
    thread_reply,
)


def ready(outcome: object) -> PostReady:
    assert isinstance(outcome, PostReady)
    return outcome


async def first_post(pipeline: Pipeline, post_format: PostFormat | None = None) -> PostReady:
    return ready(await pipeline.run_topic(TOPIC, post_format, StageLog()))


async def test_a_topic_becomes_a_short_post_by_default() -> None:
    clients = Clients()
    stages = StageLog()

    outcome = ready(await make_pipeline(clients).run_topic(TOPIC, None, stages))

    assert outcome.result.draft.post_format is PostFormat.SHORT
    assert outcome.result.draft.texts == [SHORT_TEXT]
    assert outcome.actions == list(PostAction)
    assert outcome.downgrade is None
    assert not outcome.variant
    assert outcome.stats is not None
    assert outcome.stats.facts_kept == 7
    assert stages.stages == list(PipelineStage)
    assert [len(c.calls) for c in (clients.planner, clients.extractor)] == [1, 2]
    assert [len(c.calls) for c in (clients.writer, clients.critic)] == [1, 1]


async def test_the_fact_set_has_confirmed_single_and_disputed_facts() -> None:
    outcome = await first_post(make_pipeline(Clients()))

    statuses = [fact.status for fact in outcome.fact_set.facts]

    assert statuses[0] is FactStatus.CONFIRMED
    assert statuses[1:5] == [FactStatus.SINGLE] * 4
    assert statuses[5:] == [FactStatus.DISPUTED] * 2
    assert outcome.fact_set.disputes[0].fact_ids == ["F6", "F7"]


async def test_the_default_format_comes_from_the_limits() -> None:
    clients = Clients(writer=ScriptedLLMClient(thread_reply()))
    pipeline = make_pipeline(clients, limits=make_limits(default_format=PostFormat.THREAD))

    outcome = await first_post(pipeline)

    assert outcome.result.draft.post_format is PostFormat.THREAD


async def test_a_requested_thread_with_enough_facts_is_a_thread() -> None:
    clients = Clients(writer=ScriptedLLMClient(thread_reply()))

    outcome = await first_post(make_pipeline(clients), PostFormat.THREAD)

    assert outcome.result.draft.texts == THREAD_TWEETS
    assert PostAction.THREAD not in outcome.actions
    assert {"F6", "F7"} <= set(outcome.result.draft.used_fact_ids)


async def test_a_requested_thread_with_too_few_facts_becomes_a_short_post() -> None:
    clients = Clients()
    pipeline = make_pipeline(clients, limits=make_limits(thread_min_facts=6))

    outcome = await first_post(pipeline, PostFormat.THREAD)

    assert outcome.result.draft.post_format is PostFormat.SHORT
    assert outcome.downgrade is not None
    assert (outcome.downgrade.assertable, outcome.downgrade.required) == (5, 6)
    assert PostAction.THREAD not in outcome.actions


async def test_the_thread_threshold_is_inclusive() -> None:
    clients = Clients(writer=ScriptedLLMClient(thread_reply()))
    pipeline = make_pipeline(clients, limits=make_limits(thread_min_facts=5))

    outcome = await first_post(pipeline, PostFormat.THREAD)

    assert outcome.downgrade is None
    assert outcome.result.draft.post_format is PostFormat.THREAD


@pytest.mark.parametrize(
    ("action", "reply", "expected_format"),
    [
        (PostAction.SHORTER, short_reply(SHORTER_TEXT), PostFormat.SHORT),
        (PostAction.THREAD, thread_reply(), PostFormat.THREAD),
        (PostAction.ANGLE, short_reply(SHORTER_TEXT), PostFormat.SHORT),
        (PostAction.VARIANT, short_reply(SHORTER_TEXT), PostFormat.SHORT),
    ],
)
async def test_every_button_rewrites_from_the_stored_facts(
    action: PostAction, reply: object, expected_format: PostFormat
) -> None:
    source = FakeSource("wiki", SNIPPETS)
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), reply),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )
    pipeline = make_pipeline(clients, [source])
    first = await first_post(pipeline)
    searches = source.calls

    outcome = ready(await pipeline.rework(first.draft_id, action, StageLog()))

    assert source.calls == searches
    assert len(clients.planner.calls) == 1
    assert len(clients.extractor.calls) == 2
    assert len(clients.critic.calls) == 2
    assert outcome.variant
    assert outcome.draft_id != first.draft_id
    assert outcome.fact_set == first.fact_set
    assert outcome.result.draft.post_format is expected_format
    prompt = prompt_text(clients.writer, 1)
    assert REVISION_INSTRUCTIONS[action] in prompt
    assert SHORT_TEXT in prompt


async def test_a_button_reports_its_stages() -> None:
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), short_reply(SHORTER_TEXT)),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )
    pipeline = make_pipeline(clients)
    first = await first_post(pipeline)
    stages = StageLog()

    await pipeline.rework(first.draft_id, PostAction.SHORTER, stages)

    assert stages.stages == [PipelineStage.WRITING, PipelineStage.STYLE]


async def test_another_angle_rotates_and_another_variant_keeps_it() -> None:
    clients = Clients(
        writer=ScriptedLLMClient(*(short_reply() for _ in range(4))),
        critic=ScriptedLLMClient(*(CLEAN_CRITIC for _ in range(4))),
    )
    pipeline = make_pipeline(clients)
    first = await first_post(pipeline)

    second = ready(await pipeline.rework(first.draft_id, PostAction.ANGLE, StageLog()))
    third = ready(await pipeline.rework(second.draft_id, PostAction.ANGLE, StageLog()))
    await pipeline.rework(third.draft_id, PostAction.VARIANT, StageLog())

    assert ANGLES[0] not in prompt_text(clients.writer, 0)
    assert ANGLES[0] in prompt_text(clients.writer, 1)
    assert ANGLES[1] in prompt_text(clients.writer, 2)
    assert ANGLES[1] in prompt_text(clients.writer, 3)


async def test_the_angle_wraps_around_after_the_last_one() -> None:
    count = len(ANGLES) + 1
    clients = Clients(
        writer=ScriptedLLMClient(*(short_reply() for _ in range(count + 1))),
        critic=ScriptedLLMClient(*(CLEAN_CRITIC for _ in range(count + 1))),
    )
    pipeline = make_pipeline(clients)
    current = await first_post(pipeline)
    for _ in range(count):
        current = ready(await pipeline.rework(current.draft_id, PostAction.ANGLE, StageLog()))

    assert ANGLES[0] in prompt_text(clients.writer, count)


def test_every_angle_says_claims_come_only_from_the_facts() -> None:
    for angle in ANGLES:
        assert "only from the facts" in angle
        assert "invent nothing" in angle


async def test_a_variant_of_a_variant_is_built_on_the_latest_text() -> None:
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), short_reply(SHORTER_TEXT), short_reply()),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC, CLEAN_CRITIC),
    )
    pipeline = make_pipeline(clients)
    first = await first_post(pipeline)
    second = ready(await pipeline.rework(first.draft_id, PostAction.SHORTER, StageLog()))

    await pipeline.rework(second.draft_id, PostAction.VARIANT, StageLog())

    assert SHORTER_TEXT in prompt_text(clients.writer, 2)


async def test_the_thread_button_with_too_few_facts_makes_no_call() -> None:
    clients = Clients()
    pipeline = make_pipeline(clients, limits=make_limits(thread_min_facts=6))
    first = await first_post(pipeline)

    outcome = await pipeline.rework(first.draft_id, PostAction.THREAD, StageLog())

    assert outcome == ThreadUnavailable(assertable=5, required=6)
    assert len(clients.writer.calls) == 1


async def test_an_unknown_draft_is_expired() -> None:
    outcome = await make_pipeline(Clients()).rework("0" * 12, PostAction.VARIANT, StageLog())

    assert outcome == DraftExpired()


async def test_a_draft_from_before_a_restart_is_expired() -> None:
    first = await first_post(make_pipeline(Clients()))
    restarted = make_pipeline(Clients(), store=InMemoryRunStore(20))

    outcome = await restarted.rework(first.draft_id, PostAction.SHORTER, StageLog())

    assert outcome == DraftExpired()


async def test_too_few_facts_is_reported_with_what_was_found() -> None:
    clients = Clients(extractor=ScriptedLLMClient(POOR_EXTRACTION, POOR_CONFLICT))

    outcome = await make_pipeline(clients).run_topic(TOPIC, None, StageLog())

    assert isinstance(outcome, NotEnoughFacts)
    assert outcome.extraction.assertable_count == 1
    assert outcome.disputed == 2
    assert outcome.extraction.required == 3
    assert len(outcome.extraction.fact_set.facts) == 3
    assert clients.writer.calls == []


async def test_no_enabled_source_stops_before_any_call() -> None:
    clients = Clients()

    outcome = await make_pipeline(clients, []).run_topic(TOPIC, None, StageLog())

    assert outcome == NoSources()
    assert clients.planner.calls == []


async def test_every_source_failing_is_a_research_failure() -> None:
    error = SourceUnavailableError("tavily", "HTTP 503")
    sources = [FakeSource("tavily", error=error), FakeSource("wiki", error=error)]
    clients = Clients()

    outcome = await make_pipeline(clients, sources).run_topic(TOPIC, None, StageLog())

    assert isinstance(outcome, ResearchFailed)
    assert len(outcome.failures) == 6
    assert clients.extractor.calls == []


async def test_sources_that_find_nothing_are_not_a_failure() -> None:
    error = SourceUnavailableError("tavily", "HTTP 503")
    sources = [FakeSource("tavily", error=error), FakeSource("wiki")]

    outcome = await make_pipeline(Clients(), sources).run_topic(TOPIC, None, StageLog())

    assert isinstance(outcome, NothingFound)
    assert {failure.source for failure in outcome.failures} == {"tavily"}


async def test_a_partial_failure_still_gives_a_post_and_lists_the_failures() -> None:
    error = SourceUnavailableError("tavily", "HTTP 503")
    sources = [FakeSource("wiki", [WIKI, SITE]), FakeSource("tavily", error=error)]

    outcome = await first_post(make_pipeline(Clients(), sources))

    assert len(outcome.failures) == 3
    assert {failure.kind for failure in outcome.failures} == {"SourceUnavailableError"}


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (LLMUnavailableError("down"), FailureKind.UNAVAILABLE),
        (LLMRateLimitError("slow"), FailureKind.RATE_LIMIT),
        (LLMInvalidResponseError("bad"), FailureKind.INVALID_RESPONSE),
        (LLMAuthError("key"), FailureKind.AUTH),
        (LLMError("other"), FailureKind.OTHER),
    ],
)
async def test_a_planning_error_names_the_step_and_the_kind(
    error: LLMError, kind: FailureKind
) -> None:
    clients = Clients(planner=ScriptedLLMClient(error))

    outcome = await make_pipeline(clients).run_topic(TOPIC, None, StageLog())

    assert outcome == StepFailed(stage=PipelineStage.PLANNING, kind=kind)


async def test_an_extraction_error_names_the_facts_step() -> None:
    clients = Clients(extractor=ScriptedLLMClient(LLMUnavailableError("down")))

    outcome = await make_pipeline(clients).run_topic(TOPIC, None, StageLog())

    assert outcome == StepFailed(stage=PipelineStage.FACTS, kind=FailureKind.UNAVAILABLE)


async def test_a_dispute_check_error_names_the_facts_step() -> None:
    clients = Clients(extractor=ScriptedLLMClient(FULL_EXTRACTION, LLMInvalidResponseError("bad")))

    outcome = await make_pipeline(clients).run_topic(TOPIC, None, StageLog())

    assert outcome == StepFailed(stage=PipelineStage.FACTS, kind=FailureKind.INVALID_RESPONSE)


async def test_a_writing_error_names_the_writing_step() -> None:
    clients = Clients(writer=ScriptedLLMClient(LLMUnavailableError("down")))

    outcome = await make_pipeline(clients).run_topic(TOPIC, None, StageLog())

    assert outcome == StepFailed(stage=PipelineStage.WRITING, kind=FailureKind.UNAVAILABLE)
    assert clients.critic.calls == []


async def test_a_critic_error_keeps_the_post_and_flags_it() -> None:
    clients = Clients(critic=ScriptedLLMClient(LLMUnavailableError("down")))

    outcome = await first_post(make_pipeline(clients))

    assert outcome.result.report.critic is CriticStatus.FAILED


async def test_a_regeneration_error_keeps_the_best_post() -> None:
    dashed = short_reply("8 сентября 1380 года — битва на Куликовом поле.")
    clients = Clients(writer=ScriptedLLMClient(dashed, LLMUnavailableError("down")))

    outcome = await first_post(make_pipeline(clients))

    assert outcome.result.regeneration_failed
    assert outcome.result.report.violations


async def test_a_writing_error_on_a_button_names_the_writing_step() -> None:
    clients = Clients(writer=ScriptedLLMClient(short_reply(), LLMRateLimitError("slow")))
    pipeline = make_pipeline(clients)
    first = await first_post(pipeline)

    outcome = await pipeline.rework(first.draft_id, PostAction.VARIANT, StageLog())

    assert outcome == StepFailed(stage=PipelineStage.WRITING, kind=FailureKind.RATE_LIMIT)


async def test_examples_are_read_on_every_request(tmp_path: Path) -> None:
    (tmp_path / "01.md").write_text("ПЕРВЫЙ ОБРАЗЕЦ", encoding="utf-8")
    clients = Clients(
        writer=ScriptedLLMClient(short_reply(), short_reply()),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )
    pipeline = make_pipeline(clients, limits=make_limits(examples_dir=tmp_path))
    first = await first_post(pipeline)
    (tmp_path / "01.md").write_text("ВТОРОЙ ОБРАЗЕЦ", encoding="utf-8")

    await pipeline.rework(first.draft_id, PostAction.VARIANT, StageLog())

    assert "ПЕРВЫЙ ОБРАЗЕЦ" in prompt_text(clients.writer, 0)
    assert "ВТОРОЙ ОБРАЗЕЦ" in prompt_text(clients.writer, 1)


async def test_no_examples_directory_writes_without_examples() -> None:
    clients = Clients()

    await first_post(make_pipeline(clients, limits=make_limits(examples_dir=Path("nowhere"))))

    assert "Reference posts" not in prompt_text(clients.writer, 0)


async def test_a_closing_question_is_never_allowed() -> None:
    question = short_reply("8 сентября 1380 года русских вёл Дмитрий Иванович. Кто победил?")
    clients = Clients(
        writer=ScriptedLLMClient(question, short_reply()),
        critic=ScriptedLLMClient(CLEAN_CRITIC, CLEAN_CRITIC),
    )

    outcome = await first_post(make_pipeline(clients))

    assert outcome.result.regenerations == 1


async def test_the_topic_text_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    await first_post(make_pipeline(Clients()))

    assert TOPIC not in caplog.text
    assert SHORT_TEXT not in caplog.text
    assert f"topic_length={len(TOPIC)}" in caplog.text


async def test_the_topic_is_stripped_before_planning() -> None:
    clients = Clients()

    await make_pipeline(clients).run_topic(f"  {TOPIC}  ", None, StageLog())

    assert f"  {TOPIC}" not in prompt_text(clients.planner, 0)
