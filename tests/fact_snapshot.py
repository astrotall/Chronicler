import json
from pathlib import Path

from app.domain.fact import ClaimStance, Dispute, Fact, FactSet, FactStatus, SourceRef
from pydantic import BaseModel, ConfigDict, Field

FACT_SET_KEY = "fact_set"
SNAPSHOT_SNIPPET_ID = "snapshot"
SNAPSHOT_URL_TEMPLATE = "https://{domain}/"
SNAPSHOT_ENCODING = "utf-8"


class SnapshotFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    status: FactStatus
    domains: list[str]
    weak_domains: list[str] = Field(default_factory=list)
    stance: ClaimStance = ClaimStance.ASSERTED
    rebutted_by: list[str] = Field(default_factory=list)


class FactSetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str
    facts: list[SnapshotFact]
    disputes: list[Dispute]


def unique_domains(fact: Fact) -> list[str]:
    return list(dict.fromkeys(ref.domain for ref in fact.support))


def weak_domains_of(fact: Fact) -> list[str]:
    return list(dict.fromkeys(ref.domain for ref in fact.support if ref.weak))


def snapshot_of(fact_set: FactSet) -> FactSetSnapshot:
    return FactSetSnapshot(
        topic=fact_set.topic,
        facts=[
            SnapshotFact(
                id=fact.id,
                text=fact.text,
                status=fact.status,
                domains=unique_domains(fact),
                weak_domains=weak_domains_of(fact),
                stance=fact.stance,
                rebutted_by=list(fact.rebutted_by),
            )
            for fact in fact_set.facts
        ],
        disputes=list(fact_set.disputes),
    )


def fact_set_of(snapshot: FactSetSnapshot) -> FactSet:
    return FactSet(
        topic=snapshot.topic,
        facts=[
            Fact(
                id=fact.id,
                text=fact.text,
                status=fact.status,
                stance=fact.stance,
                rebutted_by=list(fact.rebutted_by),
                support=[
                    SourceRef(
                        snippet_id=SNAPSHOT_SNIPPET_ID,
                        url=SNAPSHOT_URL_TEMPLATE.format(domain=domain),
                        domain=domain,
                        quote=fact.text,
                        weak=domain in fact.weak_domains,
                    )
                    for domain in fact.domains
                ],
            )
            for fact in snapshot.facts
        ],
        disputes=list(snapshot.disputes),
    )


def fact_set_entry(fact_set: FactSet) -> dict[str, object]:
    return {FACT_SET_KEY: snapshot_of(fact_set).model_dump(mode="json")}


def load_fact_set(path: Path) -> FactSet:
    report = json.loads(path.read_text(encoding=SNAPSHOT_ENCODING))
    return fact_set_of(FactSetSnapshot.model_validate(report[FACT_SET_KEY]))
