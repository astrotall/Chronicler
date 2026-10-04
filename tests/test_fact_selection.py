from collections.abc import Mapping

import pytest
from app.config.constants import RELEVANCE_OTHER_ASPECT
from app.domain.fact import FactStatus, SourceRef
from app.services.fact_relevance import FactRelevance
from app.services.fact_selection import (
    PRIORITY_CONFIRMED,
    PRIORITY_SINGLE,
    PRIORITY_WEAK_ONLY,
    Selection,
    cap_domains,
    fact_priority,
    select_facts,
)
from pydantic import BaseModel, ConfigDict


class Item(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: FactStatus
    support: tuple[SourceRef, ...]


def ref(domain: str, *, weak: bool = False) -> SourceRef:
    return SourceRef(
        snippet_id="s", url=f"https://{domain}/", domain=domain, quote="цитата", weak=weak
    )


def confirmed(*domains: str) -> Item:
    return Item(status=FactStatus.CONFIRMED, support=tuple(ref(domain) for domain in domains))


def single(domain: str, *, weak: bool = False) -> Item:
    return Item(status=FactStatus.SINGLE, support=(ref(domain, weak=weak),))


def select(
    items: list[Item],
    *,
    max_facts: int = 20,
    floor: int = 0,
    max_per_domain: int | None = None,
    relevance: Mapping[int, FactRelevance] | None = None,
) -> Selection:
    return select_facts(
        items,
        list(range(len(items))),
        max_facts=max_facts,
        floor=floor,
        max_per_domain=max_per_domain,
        relevance=relevance,
    )


def scored(relevance: int, aspect: str = RELEVANCE_OTHER_ASPECT) -> FactRelevance:
    return FactRelevance(relevance=relevance, aspect=aspect)


def test_priority_has_three_steps() -> None:
    assert fact_priority(confirmed("a.org", "b.org")) == PRIORITY_CONFIRMED
    assert fact_priority(single("a.org")) == PRIORITY_SINGLE
    assert fact_priority(single("a.org", weak=True)) == PRIORITY_WEAK_ONLY
    mixed = Item(status=FactStatus.SINGLE, support=(ref("a.org"), ref("b.org", weak=True)))
    assert fact_priority(mixed) == PRIORITY_SINGLE


def test_order_is_confirmed_then_single_then_weak_only_keeping_input_order() -> None:
    items = [
        single("w1.org", weak=True),
        single("a.org"),
        confirmed("x.org", "y.org"),
        single("w2.org", weak=True),
        single("b.org"),
        confirmed("z.org", "t.org"),
    ]

    assert select(items).kept == (2, 5, 1, 4, 0, 3)


def test_weak_facts_are_not_removed_when_there_is_room() -> None:
    items = [single("w.org", weak=True), single("a.org")]

    selection = select(items, max_facts=2)

    assert selection.kept == (1, 0)
    assert selection.cut_by_limit == 0


def test_weak_facts_are_the_first_to_go_at_the_limit() -> None:
    items = [single("w.org", weak=True), single("a.org"), single("b.org")]

    selection = select(items, max_facts=2)

    assert selection.kept == (1, 2)
    assert selection.cut_by_limit == 1


def test_no_cap_keeps_every_fact_up_to_the_limit() -> None:
    items = [single("a.org") for _ in range(8)]

    selection = select(items, max_facts=20, max_per_domain=None)

    assert len(selection.kept) == 8
    assert selection.cut_by_domain_cap == 0


def test_the_cap_keeps_the_most_prioritised_facts_of_a_domain() -> None:
    items = [
        single("a.org"),
        confirmed("a.org", "b.org"),
        single("c.org"),
    ]

    selection = select(items, max_per_domain=1)

    assert selection.kept == (1, 2)
    assert selection.cut_by_domain_cap == 1


def test_the_cap_applies_before_the_limit_so_other_domains_get_in() -> None:
    items = [single("a.org") for _ in range(5)] + [single("b.org"), single("c.org")]

    selection = select(items, max_facts=4, max_per_domain=2)

    assert selection.kept == (0, 1, 5, 6)
    assert selection.cut_by_domain_cap == 3
    assert selection.cut_by_limit == 0


def test_cut_by_limit_counts_what_the_cap_let_through() -> None:
    items = [single("a.org"), single("b.org"), single("c.org"), single("a.org")]

    selection = select(items, max_facts=2, max_per_domain=1)

    assert selection.kept == (0, 1)
    assert selection.cut_by_limit == 1
    assert selection.cut_by_domain_cap == 1


def test_weak_domains_count_against_the_cap_too() -> None:
    items = [single("yt.com", weak=True) for _ in range(4)] + [single("a.org")]

    selection = select(items, max_per_domain=2)

    assert selection.kept == (4, 0, 1)
    assert selection.cut_by_domain_cap == 2


def test_a_fact_is_charged_to_its_least_loaded_independent_domain() -> None:
    items = [
        confirmed("wiki.org", "life.ru"),
        confirmed("wiki.org", "life.ru"),
        confirmed("wiki.org", "life.ru"),
    ]

    selection = select(items, max_per_domain=1)

    assert selection.kept == (0, 1)
    assert selection.cut_by_domain_cap == 1


def test_a_weak_source_does_not_give_a_fact_a_second_place_to_hide() -> None:
    mixed = Item(status=FactStatus.SINGLE, support=(ref("a.org"), ref("yt.com", weak=True)))

    assert cap_domains(mixed.support) == ["a.org"]
    selection = select([single("a.org"), mixed], max_per_domain=1)

    assert selection.kept == (0,)


def test_the_cap_yields_up_to_the_floor() -> None:
    items = [single("a.org"), single("a.org"), single("b.org"), single("c.org"), single("a.org")]

    selection = select(items, max_per_domain=1, floor=3)

    assert selection.kept == (0, 2, 3)
    assert selection.restored_by_floor == 0
    assert selection.cut_by_domain_cap == 2


def test_the_cap_gives_back_the_fifth_fact_for_a_thread() -> None:
    items = [
        single("a.org"),
        single("a.org"),
        single("b.org"),
        single("c.org"),
        single("d.org"),
        single("a.org"),
    ]

    capped = select(items, max_per_domain=1, floor=4)
    thread_ready = select(items, max_per_domain=1, floor=5)

    assert len(capped.kept) == 4
    assert thread_ready.kept == (0, 1, 2, 3, 4)
    assert thread_ready.restored_by_floor == 1
    assert thread_ready.cut_by_domain_cap == 1


def test_restored_facts_come_back_in_priority_order() -> None:
    items = [single("a.org"), single("a.org"), confirmed("a.org", "a.org"), single("a.org")]

    selection = select(items, max_per_domain=1, floor=3)

    assert selection.kept == (2, 0, 1)
    assert selection.restored_by_floor == 2


def test_the_floor_never_exceeds_the_limit() -> None:
    items = [single("a.org") for _ in range(6)]

    selection = select(items, max_facts=3, floor=5, max_per_domain=1)

    assert len(selection.kept) == 3


def test_the_floor_cannot_make_up_facts() -> None:
    selection = select([single("a.org"), single("a.org")], floor=5, max_per_domain=1)

    assert len(selection.kept) == 2


def test_candidates_outside_the_index_list_are_ignored() -> None:
    items = [single("a.org"), single("b.org"), single("c.org")]

    selection = select_facts(items, [0, 2], max_facts=20, floor=0, max_per_domain=None)

    assert selection.kept == (0, 2)


@pytest.mark.parametrize("cap", [1, 2, 6])
def test_no_domain_exceeds_the_cap_when_the_floor_is_zero(cap: int) -> None:
    items = [single(f"{name}.org") for name in "aabbbbccccc"]

    kept = select(items, max_per_domain=cap).kept

    domains = [items[index].support[0].domain for index in kept]
    assert max(domains.count(domain) for domain in set(domains)) <= cap


def test_relevance_orders_facts_inside_a_level_never_across_levels() -> None:
    items = [
        single("a.org"),
        confirmed("b.org", "c.org"),
        single("d.org"),
        single("e.org", weak=True),
    ]
    relevance = {0: scored(1), 1: scored(1), 2: scored(3), 3: scored(3)}

    assert select(items, relevance=relevance).kept == (1, 2, 0, 3)


def test_higher_relevance_goes_first_inside_a_level() -> None:
    items = [single("a.org") for _ in range(4)]
    relevance = {0: scored(1), 1: scored(2), 2: scored(3), 3: scored(2)}

    assert select(items, relevance=relevance).kept == (2, 1, 3, 0)


def test_shared_aspects_take_turns() -> None:
    items = [single("a.org") for _ in range(5)]
    aspects = ["хлеб", "хлеб", "хлеб", "жильё", "жильё"]
    relevance = {index: scored(3, aspect) for index, aspect in enumerate(aspects)}

    assert select(items, relevance=relevance).kept == (0, 3, 1, 4, 2)


def test_unique_aspects_keep_the_model_order() -> None:
    items = [single("a.org") for _ in range(5)]
    relevance = {index: scored(3, f"аспект {index}") for index in range(5)}

    assert select(items, relevance=relevance).kept == (0, 1, 2, 3, 4)


def test_aspects_take_turns_only_inside_one_relevance() -> None:
    items = [single("a.org") for _ in range(4)]
    relevance = {
        0: scored(3, "хлеб"),
        1: scored(3, "хлеб"),
        2: scored(2, "жильё"),
        3: scored(3, "работа"),
    }

    assert select(items, relevance=relevance).kept == (0, 3, 1, 2)


def test_marginal_facts_follow_in_the_model_order_without_turns() -> None:
    items = [single("a.org") for _ in range(5)]
    relevance = {
        0: scored(1, "хлеб"),
        1: scored(1, "хлеб"),
        2: scored(1, "жильё"),
        3: scored(2, "хлеб"),
        4: scored(1, "хлеб"),
    }

    assert select(items, relevance=relevance).kept == (3, 0, 1, 2, 4)


def test_the_limit_now_cuts_the_least_relevant_and_most_repeated() -> None:
    items = [single(f"{name}.org") for name in "abcdef"]
    aspects = ["хлеб", "хлеб", "хлеб", "хлеб", "жильё", "работа"]
    relevance = {index: scored(3, aspect) for index, aspect in enumerate(aspects)}

    selection = select(items, max_facts=3, relevance=relevance)

    assert selection.kept == (0, 4, 5)
    assert selection.cut_by_limit == 3


def test_off_topic_facts_are_set_aside_in_every_level() -> None:
    items = [confirmed("a.org", "b.org"), single("c.org"), single("d.org"), single("e.org")]
    relevance = {
        0: FactRelevance(relevance=3, about_source=True),
        1: FactRelevance(relevance=3, outside_period=True),
        2: scored(0),
        3: scored(1),
    }

    selection = select(items, relevance=relevance)

    assert selection.kept == (3,)
    assert (selection.set_aside_off_topic, selection.off_topic_restored) == (3, 0)


def test_the_floor_brings_back_capped_facts_before_off_topic_ones_and_puts_these_last() -> None:
    items = [single("a.org"), single("a.org"), confirmed("b.org", "c.org"), single("d.org")]
    relevance = {0: scored(3), 1: scored(3), 2: FactRelevance(about_source=True), 3: scored(0)}

    selection = select(items, max_per_domain=1, floor=4, relevance=relevance)

    assert selection.kept == (0, 1, 2, 3)
    assert selection.restored_by_floor == 1
    assert (selection.set_aside_off_topic, selection.off_topic_restored) == (0, 2)


def test_the_floor_takes_off_topic_facts_in_priority_order() -> None:
    items = [single("a.org"), single("b.org", weak=True), confirmed("c.org", "d.org")]
    relevance = {0: scored(0), 1: scored(0), 2: scored(0)}

    selection = select(items, floor=2, relevance=relevance)

    assert selection.kept == (2, 0)
    assert selection.set_aside_off_topic == 1


def test_unscored_facts_count_as_useful_context() -> None:
    items = [single("a.org") for _ in range(3)]
    relevance = {1: scored(3), 2: scored(1)}

    assert select(items, relevance=relevance).kept == (1, 0, 2)


@pytest.mark.parametrize("relevance", [None, {}])
def test_without_scores_the_order_is_the_old_one(
    relevance: dict[int, FactRelevance] | None,
) -> None:
    items = [
        single("a.org", weak=True),
        single("b.org"),
        confirmed("c.org", "d.org"),
        single("e.org"),
    ]

    assert select(items, relevance=relevance).kept == (2, 1, 3, 0)
