import pytest
from app.config.constants import STATE_ID_BYTES
from app.domain.draft import PostFormat
from app.domain.fact import FactSet
from app.services.run_store import InMemoryRunStore

from style_helpers import FACT_SET, make_draft

DRAFT = make_draft(["Первый текст."], PostFormat.SHORT)
OTHER = make_draft(["Второй текст."], PostFormat.SHORT)


async def test_a_stored_draft_comes_back_with_its_facts_and_angle() -> None:
    store = InMemoryRunStore(2)
    run_id = await store.add_run(FACT_SET)

    draft_id = await store.add_draft(run_id, DRAFT, 3)

    assert draft_id is not None
    stored = await store.get_draft(draft_id)
    assert stored is not None
    assert (stored.run_id, stored.draft, stored.angle_index) == (run_id, DRAFT, 3)
    assert stored.fact_set == FACT_SET


async def test_ids_are_random_hex_and_fit_a_callback() -> None:
    store = InMemoryRunStore(2)
    run_id = await store.add_run(FACT_SET)
    first = await store.add_draft(run_id, DRAFT, None)
    second = await store.add_draft(run_id, DRAFT, None)

    assert first is not None
    assert second is not None
    assert first != second
    assert len(first) == STATE_ID_BYTES * 2
    int(first, 16)


async def test_the_oldest_run_is_evicted_with_its_drafts() -> None:
    store = InMemoryRunStore(2)
    old_run = await store.add_run(FACT_SET)
    old_draft = await store.add_draft(old_run, DRAFT, None)
    await store.add_run(FACT_SET)
    await store.add_run(FACT_SET)

    assert old_draft is not None
    assert await store.get_draft(old_draft) is None
    assert await store.add_draft(old_run, OTHER, None) is None


async def test_a_used_run_survives_newer_runs() -> None:
    store = InMemoryRunStore(2)
    used_run = await store.add_run(FACT_SET)
    used_draft = await store.add_draft(used_run, DRAFT, None)
    idle_run = await store.add_run(FACT_SET)
    idle_draft = await store.add_draft(idle_run, DRAFT, None)
    assert used_draft is not None
    assert idle_draft is not None
    await store.get_draft(used_draft)

    await store.add_run(FACT_SET)

    assert await store.get_draft(used_draft) is not None
    assert await store.get_draft(idle_draft) is None


async def test_the_drafts_of_a_run_are_capped() -> None:
    store = InMemoryRunStore(1, max_drafts_per_run=2)
    run_id = await store.add_run(FACT_SET)
    ids = [await store.add_draft(run_id, DRAFT, None) for _ in range(3)]

    assert ids[0] is not None
    assert await store.get_draft(ids[0]) is None
    for draft_id in ids[1:]:
        assert draft_id is not None
        assert await store.get_draft(draft_id) is not None


async def test_an_empty_fact_set_can_be_stored() -> None:
    store = InMemoryRunStore(1)

    run_id = await store.add_run(FactSet(topic="t", facts=[], disputes=[]))

    assert await store.add_draft(run_id, DRAFT, None) is not None


@pytest.mark.parametrize(("runs", "drafts"), [(0, 1), (1, 0)])
def test_a_store_without_room_is_rejected(runs: int, drafts: int) -> None:
    with pytest.raises(ValueError, match="at least one"):
        InMemoryRunStore(runs, max_drafts_per_run=drafts)
