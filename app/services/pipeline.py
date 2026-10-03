import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import NamedTuple, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field

from app.config.settings import Settings
from app.domain.draft import Draft, PostFormat, Revision
from app.domain.fact import FactSet, InsufficientFacts
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
    ReworkOutcome,
    StepFailed,
    StoredDraft,
    ThreadDowngrade,
    ThreadUnavailable,
    TopicOutcome,
)
from app.domain.research import ResearchResult
from app.domain.style import StyleResult
from app.llm.client import LLMClient
from app.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRequestError,
    LLMUnavailableError,
)
from app.prompts.revisions import ANGLES, REVISION_INSTRUCTIONS
from app.prompts.writing import disputed_ids
from app.services.facts import FactLimits, extract_facts
from app.services.generator import WritingLimits, write_draft
from app.services.query_planning import plan_queries
from app.services.run_store import RunStore
from app.services.style import load_examples
from app.services.style_review import StyleLimits, review_style

logger = logging.getLogger(__name__)

FAILURE_KINDS: tuple[tuple[type[LLMError], FailureKind], ...] = (
    (LLMConfigError, FailureKind.CONFIG),
    (LLMAuthError, FailureKind.AUTH),
    (LLMRateLimitError, FailureKind.RATE_LIMIT),
    (LLMUnavailableError, FailureKind.UNAVAILABLE),
    (LLMInvalidResponseError, FailureKind.INVALID_RESPONSE),
    (LLMRequestError, FailureKind.REQUEST),
)


class Progress(Protocol):
    async def __call__(self, stage: PipelineStage) -> None: ...


class Researcher(Protocol):
    @property
    def source_names(self) -> list[str]: ...

    async def research(self, queries: Sequence[str]) -> ResearchResult: ...


class PipelineClients(NamedTuple):
    planner: LLMClient
    extractor: LLMClient
    writer: LLMClient
    critic: LLMClient


class PipelineLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    facts: FactLimits
    writing: WritingLimits
    style: StyleLimits
    examples_dir: Path
    examples_max: int = Field(ge=0)
    thread_min_facts: int = Field(ge=1)
    default_format: PostFormat

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            facts=FactLimits.from_settings(settings),
            writing=WritingLimits.from_settings(settings),
            style=StyleLimits.from_settings(settings),
            examples_dir=settings.examples_dir,
            examples_max=settings.examples_max,
            thread_min_facts=settings.thread_min_facts,
            default_format=PostFormat(settings.post_default_format),
        )


class StageError(Exception):
    def __init__(self, stage: PipelineStage, kind: FailureKind) -> None:
        self.stage = stage
        self.kind = kind
        super().__init__(f"{stage}: {kind}")


def failure_kind(error: LLMError) -> FailureKind:
    for error_type, kind in FAILURE_KINDS:
        if isinstance(error, error_type):
            return kind
    return FailureKind.OTHER


async def staged[T](
    stage: PipelineStage, progress: Progress, call: Callable[[], Awaitable[T]]
) -> T:
    await progress(stage)
    try:
        return await call()
    except LLMError as error:
        logger.warning("pipeline step failed stage=%s error=%s", stage, type(error).__name__)
        raise StageError(stage, failure_kind(error)) from error


def assertable_count(fact_set: FactSet) -> int:
    disputed = disputed_ids(fact_set)
    return sum(1 for fact in fact_set.facts if fact.id not in disputed)


def sources_all_failed(result: ResearchResult, source_names: Sequence[str]) -> bool:
    failed = {failure.source for failure in result.failures}
    return bool(source_names) and failed >= set(source_names)


def next_angle_index(current: int | None) -> int:
    return 0 if current is None else (current + 1) % len(ANGLES)


class Pipeline:
    def __init__(
        self,
        clients: PipelineClients,
        researcher: Researcher,
        store: RunStore,
        limits: PipelineLimits,
    ) -> None:
        self._clients = clients
        self._researcher = researcher
        self._store = store
        self._limits = limits

    @property
    def default_format(self) -> PostFormat:
        return self._limits.default_format

    async def run_topic(
        self, topic: str, post_format: PostFormat | None, progress: Progress
    ) -> TopicOutcome:
        requested = post_format or self._limits.default_format
        try:
            outcome = await self._run_topic(topic.strip(), requested, progress)
        except StageError as error:
            outcome = StepFailed(stage=error.stage, kind=error.kind)
        logger.info(
            "topic finished topic_length=%d format=%s outcome=%s",
            len(topic.strip()),
            requested,
            type(outcome).__name__,
        )
        return outcome

    async def rework(self, draft_id: str, action: PostAction, progress: Progress) -> ReworkOutcome:
        try:
            outcome = await self._rework(draft_id, action, progress)
        except StageError as error:
            outcome = StepFailed(stage=error.stage, kind=error.kind)
        logger.info("rework finished action=%s outcome=%s", action, type(outcome).__name__)
        return outcome

    def actions(self, fact_set: FactSet, draft: Draft) -> list[PostAction]:
        return [
            action
            for action in PostAction
            if action is not PostAction.THREAD
            or (
                draft.post_format is not PostFormat.THREAD
                and assertable_count(fact_set) >= self._limits.thread_min_facts
            )
        ]

    async def _run_topic(
        self, topic: str, requested: PostFormat, progress: Progress
    ) -> TopicOutcome:
        source_names = self._researcher.source_names
        if not source_names:
            return NoSources()
        queries = await staged(
            PipelineStage.PLANNING,
            progress,
            lambda: plan_queries(self._clients.planner, topic),
        )
        research = await staged(
            PipelineStage.RESEARCH, progress, lambda: self._researcher.research(queries)
        )
        if not research.snippets:
            if sources_all_failed(research, source_names):
                return ResearchFailed(failures=research.failures)
            return NothingFound(failures=research.failures)
        extraction = await staged(
            PipelineStage.FACTS,
            progress,
            lambda: extract_facts(
                self._clients.extractor, topic, research.snippets, self._limits.facts
            ),
        )
        if isinstance(extraction, InsufficientFacts):
            return NotEnoughFacts(
                extraction=extraction,
                disputed=len(extraction.fact_set.facts) - extraction.assertable_count,
                failures=research.failures,
            )
        fact_set = extraction.fact_set
        post_format, downgrade = self._choose_format(fact_set, requested)
        run_id = await self._store.add_run(fact_set)
        result = await self._write(fact_set, post_format, progress, angle_index=None)
        draft_id = await self._store.add_draft(run_id, result.draft, None)
        if draft_id is None:
            raise RuntimeError("the run was evicted before its first draft was stored")
        return PostReady(
            draft_id=draft_id,
            result=result,
            fact_set=fact_set,
            actions=self.actions(fact_set, result.draft),
            failures=research.failures,
            downgrade=downgrade,
            stats=extraction.stats,
        )

    async def _rework(self, draft_id: str, action: PostAction, progress: Progress) -> ReworkOutcome:
        stored = await self._store.get_draft(draft_id)
        if stored is None:
            return DraftExpired()
        post_format = stored.draft.post_format
        angle_index = stored.angle_index
        if action is PostAction.THREAD:
            assertable = assertable_count(stored.fact_set)
            if assertable < self._limits.thread_min_facts:
                return ThreadUnavailable(
                    assertable=assertable, required=self._limits.thread_min_facts
                )
            post_format = PostFormat.THREAD
        if action is PostAction.ANGLE:
            angle_index = next_angle_index(angle_index)
        result = await self._write(
            stored.fact_set,
            post_format,
            progress,
            angle_index=angle_index,
            revision=self._revision(stored, action),
        )
        new_id = await self._store.add_draft(stored.run_id, result.draft, angle_index)
        if new_id is None:
            return DraftExpired()
        return PostReady(
            draft_id=new_id,
            result=result,
            fact_set=stored.fact_set,
            actions=self.actions(stored.fact_set, result.draft),
            variant=True,
        )

    def _choose_format(
        self, fact_set: FactSet, requested: PostFormat
    ) -> tuple[PostFormat, ThreadDowngrade | None]:
        assertable = assertable_count(fact_set)
        if requested is PostFormat.THREAD and assertable < self._limits.thread_min_facts:
            return PostFormat.SHORT, ThreadDowngrade(
                assertable=assertable, required=self._limits.thread_min_facts
            )
        return requested, None

    def _revision(self, stored: StoredDraft, action: PostAction) -> Revision:
        return Revision(instruction=REVISION_INSTRUCTIONS[action], previous=stored.draft.texts)

    async def _write(
        self,
        fact_set: FactSet,
        post_format: PostFormat,
        progress: Progress,
        *,
        angle_index: int | None,
        revision: Revision | None = None,
    ) -> StyleResult:
        examples = load_examples(self._limits.examples_dir, self._limits.examples_max)
        angle = ANGLES[angle_index] if angle_index is not None else None
        draft = await staged(
            PipelineStage.WRITING,
            progress,
            lambda: write_draft(
                self._clients.writer,
                fact_set,
                post_format,
                self._limits.writing,
                examples,
                angle=angle,
                revision=revision,
            ),
        )
        return await staged(
            PipelineStage.STYLE,
            progress,
            lambda: review_style(
                self._clients.writer,
                self._clients.critic,
                draft,
                fact_set,
                self._limits.writing,
                self._limits.style,
                examples,
                angle=angle,
                allow_closing_question=False,
            ),
        )
