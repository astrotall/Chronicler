import secrets
from collections import OrderedDict
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.config.constants import STATE_ID_BYTES, STATE_MAX_DRAFTS_PER_RUN
from app.domain.draft import Draft
from app.domain.fact import FactSet
from app.domain.pipeline import StoredDraft


class RunStore(Protocol):
    async def add_run(self, fact_set: FactSet) -> str: ...

    async def add_draft(self, run_id: str, draft: Draft, angle_index: int | None) -> str | None: ...

    async def get_draft(self, draft_id: str) -> StoredDraft | None: ...


class DraftEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft: Draft
    angle_index: int | None = Field(default=None, ge=0)


class RunEntry:
    def __init__(self, fact_set: FactSet) -> None:
        self.fact_set = fact_set
        self.drafts: OrderedDict[str, DraftEntry] = OrderedDict()


def new_id() -> str:
    return secrets.token_hex(STATE_ID_BYTES)


class InMemoryRunStore:
    def __init__(self, max_runs: int, max_drafts_per_run: int = STATE_MAX_DRAFTS_PER_RUN) -> None:
        if max_runs < 1 or max_drafts_per_run < 1:
            raise ValueError("the store must keep at least one run and one draft")
        self._max_runs = max_runs
        self._max_drafts = max_drafts_per_run
        self._runs: OrderedDict[str, RunEntry] = OrderedDict()
        self._draft_runs: dict[str, str] = {}

    async def add_run(self, fact_set: FactSet) -> str:
        run_id = new_id()
        self._runs[run_id] = RunEntry(fact_set)
        while len(self._runs) > self._max_runs:
            _, evicted = self._runs.popitem(last=False)
            for draft_id in evicted.drafts:
                self._draft_runs.pop(draft_id, None)
        return run_id

    async def add_draft(self, run_id: str, draft: Draft, angle_index: int | None) -> str | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        draft_id = new_id()
        run.drafts[draft_id] = DraftEntry(draft=draft, angle_index=angle_index)
        self._draft_runs[draft_id] = run_id
        self._runs.move_to_end(run_id)
        while len(run.drafts) > self._max_drafts:
            evicted_id, _ = run.drafts.popitem(last=False)
            self._draft_runs.pop(evicted_id, None)
        return draft_id

    async def get_draft(self, draft_id: str) -> StoredDraft | None:
        run_id = self._draft_runs.get(draft_id)
        if run_id is None:
            return None
        run = self._runs[run_id]
        entry = run.drafts[draft_id]
        self._runs.move_to_end(run_id)
        return StoredDraft(
            draft_id=draft_id,
            run_id=run_id,
            fact_set=run.fact_set,
            draft=entry.draft,
            angle_index=entry.angle_index,
        )
