import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from app.config.constants import LLMStep
from app.domain.draft import Draft, DraftPart, PostFormat
from app.domain.fact import FactSet
from app.domain.llm import LLMResult, Message
from app.domain.style import StyleResult, Violation
from app.llm.client import LLMClient
from app.llm.factory import LLMClientFactory, build_http_client
from app.services.generator import WritingLimits, write_draft
from app.services.quote_check import text_segments
from app.services.style_critic import CriticOutcome, critique_draft
from app.services.style_filter import check_draft
from app.services.style_review import StyleLimits, review_style
from pydantic import BaseModel

from live_keys import LiveKeys, skip_without_key
from test_generator_live import APOLLO_FACTS, KULIKOVO_FACTS, live_settings

KEYS = LiveKeys()
COMPARISONS = Path("data/comparisons")
CYCLE_SAMPLES: dict[PostFormat, int] = {PostFormat.THREAD: 2, PostFormat.SHORT: 2}

logger = logging.getLogger(__name__)


class KnownDefect(NamedTuple):
    source: str
    draft: int
    part: int
    phrase: str


class DraftSource(NamedTuple):
    name: str
    fact_set: FactSet


SYNTHETIC = "synthetic_fillers"
SYNTHETIC_DRAFT = Draft(
    post_format=PostFormat.THREAD,
    parts=[
        DraftPart(
            text="Мамай рассчитывал соединиться с войском литовского князя Ягайло, но тот не "
            "успел к битве. Союзник не пришёл. Это решило многое."
        ),
        DraftPart(
            text="Перед сражением состоялся поединок инока Пересвета с ордынским богатырём "
            "Челубеем. Эта деталь держит внимание даже спустя столетия."
        ),
    ],
    used_fact_ids=["F6", "F8"],
    unverified_numbers=[],
    length_violations=[],
    attempts=1,
)
BEFORE = "his21_before_kulikovo"
EXAMPLES = "his21_examples_kulikovo"
KNOWN_DEFECTS = [
    KnownDefect(BEFORE, 1, 1, "По преданию"),
    KnownDefect(BEFORE, 2, 3, "Мне это кажется важной деталью"),
    KnownDefect(BEFORE, 2, 6, "Я не берусь выбирать между этими цифрами"),
    KnownDefect(BEFORE, 2, 8, "Победа на Дону не отменила ни зависимости, ни новых походов"),
    KnownDefect(BEFORE, 2, 8, "через два года"),
    KnownDefect(BEFORE, 3, 4, "Я не берусь выбирать между этими цифрами"),
    KnownDefect(BEFORE, 3, 5, "Победа на Дону не остановила Орду"),
    KnownDefect(BEFORE, 3, 5, "через два года"),
    KnownDefect(EXAMPLES, 1, 1, "вёл их"),
    KnownDefect(EXAMPLES, 3, 1, "По преданию"),
    KnownDefect(EXAMPLES, 3, 2, "Место известно точно"),
    KnownDefect(EXAMPLES, 3, 5, "Уже в 1382 году"),
    KnownDefect(SYNTHETIC, 0, 1, "Это решило многое"),
    KnownDefect(SYNTHETIC, 0, 2, "Эта деталь держит внимание даже спустя столетия"),
]
BAD_SOURCES = [DraftSource(BEFORE, KULIKOVO_FACTS), DraftSource(EXAMPLES, KULIKOVO_FACTS)]
CLEAN_SOURCES = [
    DraftSource("his21_dash_kulikovo", KULIKOVO_FACTS),
    DraftSource("his21_dash_apollo11", APOLLO_FACTS),
    DraftSource("his21_after_apollo11", APOLLO_FACTS),
]


class ReplyRecordingClient:
    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.replies: list[BaseModel] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult:
        return await self._inner.complete(messages, temperature=temperature, max_tokens=max_tokens)

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        reply = await self._inner.complete_json(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )
        self.replies.append(reply)
        return reply


def as_llm_client(client: ReplyRecordingClient) -> LLMClient:
    return client


def load_drafts(name: str) -> list[Draft]:
    path = COMPARISONS / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"{path} is not available, it is a local comparison file")
    return [Draft.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def folded(text: str) -> str:
    return " ".join(text_segments(text))


def covers(violation: Violation, defect: KnownDefect) -> bool:
    if violation.part != defect.part or violation.excerpt is None:
        return False
    excerpt = folded(violation.excerpt)
    phrase = folded(defect.phrase)
    return phrase in excerpt or excerpt in phrase


def violation_record(violation: Violation) -> dict[str, object]:
    return violation.model_dump(mode="json")


def save(name: str, payload: object) -> None:
    COMPARISONS.mkdir(parents=True, exist_ok=True)
    (COMPARISONS / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def critique_all(
    client: LLMClient, drafts: Sequence[Draft], fact_set: FactSet
) -> list[CriticOutcome]:
    limits = StyleLimits.from_settings(live_settings())
    return [
        await critique_draft(client, draft.texts, fact_set, limits.critic_max_findings)
        for draft in drafts
    ]


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_critic_finds_known_defects() -> None:
    settings = live_settings()
    sources = {source.name: (load_drafts(source.name), source.fact_set) for source in BAD_SOURCES}
    sources[SYNTHETIC] = ([SYNTHETIC_DRAFT], KULIKOVO_FACTS)
    async with build_http_client(settings) as http_client:
        critic = ReplyRecordingClient(
            LLMClientFactory(settings, http_client).get_client(LLMStep.STYLE_CRITIQUE)
        )
        outcomes = {
            name: await critique_all(as_llm_client(critic), drafts, fact_set)
            for name, (drafts, fact_set) in sources.items()
        }
    report: list[dict[str, object]] = []
    for defect in KNOWN_DEFECTS:
        outcome = outcomes[defect.source][defect.draft]
        matches = [violation for violation in outcome.violations if covers(violation, defect)]
        report.append(
            {
                "source": defect.source,
                "draft": defect.draft,
                "part": defect.part,
                "phrase": defect.phrase,
                "found": bool(matches),
                "findings": [violation_record(violation) for violation in matches],
            }
        )
    every_finding = {
        name: [
            {
                "draft": index,
                "texts": sources[name][0][index].texts,
                "code": [violation_record(item) for item in check_draft(sources[name][0][index])],
                "critic": [violation_record(item) for item in outcome.violations],
                "dropped": outcome.dropped,
                "withdrawn": outcome.withdrawn,
            }
            for index, outcome in enumerate(name_outcomes)
        ]
        for name, name_outcomes in outcomes.items()
    }
    save("his7_known_defects.json", {"defects": report, "drafts": every_finding})
    found = sum(1 for item in report if item["found"])
    logger.info(
        "live known defects found=%d of %d requests=%d", found, len(report), len(critic.replies)
    )
    assert found >= 1


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_critic_on_relatively_clean_drafts() -> None:
    settings = live_settings()
    sources = {source.name: (load_drafts(source.name), source.fact_set) for source in CLEAN_SOURCES}
    async with build_http_client(settings) as http_client:
        critic = ReplyRecordingClient(
            LLMClientFactory(settings, http_client).get_client(LLMStep.STYLE_CRITIQUE)
        )
        outcomes = {
            name: await critique_all(as_llm_client(critic), drafts, fact_set)
            for name, (drafts, fact_set) in sources.items()
        }
    payload = {
        name: [
            {
                "draft": index,
                "texts": sources[name][0][index].texts,
                "code": [violation_record(item) for item in check_draft(sources[name][0][index])],
                "critic": [violation_record(item) for item in outcome.violations],
                "dropped": outcome.dropped,
                "withdrawn": outcome.withdrawn,
            }
            for index, outcome in enumerate(name_outcomes)
        ]
        for name, name_outcomes in outcomes.items()
    }
    save("his7_clean_drafts.json", payload)
    total = sum(len(outcome.violations) for items in outcomes.values() for outcome in items)
    logger.info("live clean drafts findings=%d requests=%d", total, len(critic.replies))


def cycle_record(
    before: Draft, result: StyleResult, critic_replies: Sequence[object]
) -> dict[str, object]:
    return {
        "format": before.post_format,
        "before": before.texts,
        "before_code": [violation_record(item) for item in check_draft(before)],
        "after": result.draft.texts,
        "attempts": result.attempts,
        "chosen_attempt": result.chosen_attempt,
        "regenerations": result.regenerations,
        "regeneration_failed": result.regeneration_failed,
        "critic": result.report.critic,
        "residual": [violation_record(item) for item in result.report.violations],
        "critic_replies": critic_replies,
        "unverified_numbers": result.draft.unverified_numbers,
        "length_violations": [
            item.model_dump(mode="json") for item in result.draft.length_violations
        ],
    }


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_full_style_cycle() -> None:
    settings = live_settings()
    writing_limits = WritingLimits.from_settings(settings)
    style_limits = StyleLimits.from_settings(settings)
    records: list[dict[str, object]] = []
    async with build_http_client(settings) as http_client:
        factory = LLMClientFactory(settings, http_client)
        writer = ReplyRecordingClient(factory.get_client(LLMStep.WRITING))
        critic = ReplyRecordingClient(factory.get_client(LLMStep.STYLE_CRITIQUE))
        for post_format, count in CYCLE_SAMPLES.items():
            for _ in range(count):
                before = await write_draft(
                    as_llm_client(writer), KULIKOVO_FACTS, post_format, writing_limits
                )
                seen = len(critic.replies)
                result = await review_style(
                    as_llm_client(writer),
                    as_llm_client(critic),
                    before,
                    KULIKOVO_FACTS,
                    writing_limits,
                    style_limits,
                )
                replies = [reply.model_dump(mode="json") for reply in critic.replies[seen:]]
                records.append(cycle_record(before, result, replies))
                assert result.draft.texts
    save("his7_full_cycle.json", records)
    logger.info(
        "live full cycle writer_requests=%d critic_requests=%d",
        len(writer.replies),
        len(critic.replies),
    )


BAD_CYCLE_STARTS: list[tuple[str, int]] = [
    (EXAMPLES, 3),
    (EXAMPLES, 1),
    (BEFORE, 2),
    (SYNTHETIC, 0),
]


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_style_cycle_from_known_bad_drafts() -> None:
    settings = live_settings()
    writing_limits = WritingLimits.from_settings(settings)
    style_limits = StyleLimits.from_settings(settings)
    starts = [
        SYNTHETIC_DRAFT if name == SYNTHETIC else load_drafts(name)[index]
        for name, index in BAD_CYCLE_STARTS
    ]
    records: list[dict[str, object]] = []
    async with build_http_client(settings) as http_client:
        factory = LLMClientFactory(settings, http_client)
        writer = ReplyRecordingClient(factory.get_client(LLMStep.WRITING))
        critic = ReplyRecordingClient(factory.get_client(LLMStep.STYLE_CRITIQUE))
        for before in starts:
            seen = len(critic.replies)
            result = await review_style(
                as_llm_client(writer),
                as_llm_client(critic),
                before,
                KULIKOVO_FACTS,
                writing_limits,
                style_limits,
            )
            replies = [reply.model_dump(mode="json") for reply in critic.replies[seen:]]
            records.append(cycle_record(before, result, replies))
    save("his7_bad_cycle.json", records)
    logger.info(
        "live bad cycle writer_requests=%d critic_requests=%d",
        len(writer.replies),
        len(critic.replies),
    )
    assert all(record["regenerations"] for record in records)
