import json
from pathlib import Path

import pytest
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from pydantic import ValidationError

from fact_snapshot import (
    FACT_SET_KEY,
    fact_set_entry,
    fact_set_of,
    load_fact_set,
    snapshot_of,
)

QUOTE = "SECRET-QUOTE: verbatim source text"


def make_fact(fact_id: str, text: str, status: FactStatus, *domains: str) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        status=status,
        support=[
            SourceRef(snippet_id="S1", url=f"https://{domain}/page", domain=domain, quote=QUOTE)
            for domain in domains
        ],
    )


FACT_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        make_fact(
            "F1", "Битва была в 1380 году.", FactStatus.CONFIRMED, "wikipedia.org", "life.ru"
        ),
        make_fact("F2", "Командовал князь.", FactStatus.SINGLE, "kp.ru"),
        make_fact(
            "F3", "Было 60 000 воинов.", FactStatus.DISPUTED, "wikipedia.org", "wikipedia.org"
        ),
        make_fact("F4", "Было 150 000 воинов.", FactStatus.DISPUTED, "prlib.ru"),
    ],
    disputes=[Dispute(fact_ids=["F3", "F4"], explanation="Оценки расходятся.")],
)


def test_a_fact_set_survives_a_write_and_a_read(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"stats": {"snippets": 3}, **fact_set_entry(FACT_SET)}, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_fact_set(path)

    assert loaded.topic == FACT_SET.topic
    assert [(fact.id, fact.text, fact.status) for fact in loaded.facts] == [
        (fact.id, fact.text, fact.status) for fact in FACT_SET.facts
    ]
    assert loaded.disputes == FACT_SET.disputes


def test_the_domains_are_kept_once_each_in_order() -> None:
    loaded = fact_set_of(snapshot_of(FACT_SET))

    assert [[ref.domain for ref in fact.support] for fact in loaded.facts] == [
        ["wikipedia.org", "life.ru"],
        ["kp.ru"],
        ["wikipedia.org"],
        ["prlib.ru"],
    ]
    assert all(ref.url.startswith("https://") for fact in loaded.facts for ref in fact.support)


def test_the_quotes_are_not_saved() -> None:
    saved = json.dumps(fact_set_entry(FACT_SET), ensure_ascii=False)

    assert QUOTE not in saved
    assert "https://wikipedia.org/page" not in saved
    assert "wikipedia.org" in saved


def test_an_empty_fact_set_round_trips() -> None:
    empty = FactSet(topic="Пусто", facts=[], disputes=[])

    assert fact_set_of(snapshot_of(empty)) == empty


def test_a_report_without_a_fact_set_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"stats": {}}), encoding="utf-8")

    with pytest.raises(KeyError, match=FACT_SET_KEY):
        load_fact_set(path)


def test_a_fact_with_an_unknown_status_is_rejected(tmp_path: Path) -> None:
    raw = snapshot_of(FACT_SET).model_dump(mode="json")
    raw["facts"][0]["status"] = "maybe"
    path = tmp_path / "report.json"
    path.write_text(json.dumps({FACT_SET_KEY: raw}), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_fact_set(path)
