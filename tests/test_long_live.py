import json
import logging
from pathlib import Path
from typing import NamedTuple

import pytest
from app.config.constants import LLMStep
from app.config.settings import Settings
from app.domain.draft import Draft, LengthIssue, PostFormat
from app.domain.fact import Dispute, FactSet, FactStatus
from app.domain.style import StyleResult
from app.llm.factory import LLMClientFactory, build_http_client
from app.services.generator import WritingLimits, write_draft
from app.services.style_review import StyleLimits, review_style

from fact_snapshot import fact_set_entry
from live_keys import LiveKeys, skip_without_key
from llm_helpers import RecordingLLMClient, as_client
from test_generator_live import KULIKOVO_FACTS, SourcePair, live_fact, live_settings

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
OUTPUT_TEMPLATE = "his30_{slug}.json"
SAMPLES_PER_SET = 3
NO_EXAMPLES: tuple[str, ...] = ()
FIXTURE_NOT_SOURCED = (
    "The fact sets are written from the model's knowledge, not taken from sources. They check "
    "the form of the text only, never its factual accuracy."
)
FIRST_PHRASE_MAX_CHARS = 120

logger = logging.getLogger(__name__)

SOVIET = SourcePair(
    ru_url="https://ru.wikipedia.org/wiki/СССР",
    en_url="https://en.wikipedia.org/wiki/Soviet_Union",
)

SOVIET_DAY_FACTS = FactSet(
    topic="Как выглядел обычный день советского человека в 1930-е",
    facts=[
        live_fact(
            "F1",
            "В ночь на 31 августа 1935 года шахтёр Алексей Стаханов добыл в Донбассе за смену "
            "102 тонны угля при норме 7 тонн.",
            SOVIET,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F2",
            "Рабочих, которые перевыполняли нормы, называли стахановцами, им давали премии и "
            "более высокий заработок.",
            SOVIET,
        ),
        live_fact(
            "F3",
            "В декабре 1932 года в городах ввели внутренние паспорта и прописку.",
            SOVIET,
        ),
        live_fact(
            "F4",
            "Многие горожане жили в коммунальных квартирах: у семьи была комната, а кухня, "
            "коридор и ванная были общими.",
            SOVIET,
        ),
        live_fact(
            "F5",
            "Санитарной нормой жилой площади считали 9 квадратных метров на человека, но во "
            "многих городах на человека приходилось заметно меньше.",
            SOVIET,
        ),
        live_fact(
            "F6",
            "Карточки на хлеб отменили в январе 1935 года, а на остальные продовольственные "
            "товары в октябре 1935 года.",
            SOVIET,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F7",
            "С 1931 по 1936 год магазины Торгсина принимали за товары золото, серебро и "
            "иностранную валюту.",
            SOVIET,
        ),
        live_fact(
            "F8",
            "Часть работников, в том числе руководителей и учёных, снабжали через закрытые "
            "распределители.",
            SOVIET,
        ),
        live_fact(
            "F9",
            "15 мая 1935 года в Москве открылась первая линия метро.",
            SOVIET,
        ),
        live_fact(
            "F10",
            "В большинстве советских городов главным видом городского транспорта оставался "
            "трамвай.",
            SOVIET,
        ),
        live_fact(
            "F11",
            "Постановления 1931 и 1932 годов вернули в школу классно-урочную систему, "
            "отдельные предметы и твёрдые программы.",
            SOVIET,
        ),
        live_fact(
            "F12",
            "С 1930 года в СССР ввели обязательное всеобщее начальное обучение.",
            SOVIET,
        ),
        live_fact(
            "F13",
            "Во многих городских квартирах висела радиоточка, репродуктор, по которому "
            "передавали новости, концерты и сводки.",
            SOVIET,
        ),
        live_fact(
            "F14",
            "Фильм «Чапаев» вышел на экраны в 1934 году и стал одним из самых популярных "
            "фильмов десятилетия.",
            SOVIET,
        ),
        live_fact(
            "F15",
            "Ткани и готовая одежда были в дефиците, поэтому вещи часто перешивали и носили "
            "годами.",
            SOVIET,
        ),
        live_fact(
            "F16",
            "Рабочие и служащие носили гимнастёрки, косоворотки и кепки, а женщины платья, "
            "косынки и береты.",
            SOVIET,
        ),
        live_fact(
            "F17",
            "Перепись 1937 года насчитала в СССР около 162 миллионов человек.",
            SOVIET,
            FactStatus.DISPUTED,
        ),
        live_fact(
            "F18",
            "Перепись 1939 года насчитала в СССР около 170 миллионов человек.",
            SOVIET,
            FactStatus.DISPUTED,
        ),
    ],
    disputes=[
        Dispute(
            fact_ids=["F17", "F18"],
            explanation="Две переписи называют разную численность населения СССР.",
        )
    ],
)


class LongCase(NamedTuple):
    slug: str
    fact_set: FactSet


LONG_CASES = [
    LongCase(slug="soviet_day_1930s", fact_set=SOVIET_DAY_FACTS),
    LongCase(slug="kulikovo", fact_set=KULIKOVO_FACTS),
]


class Sample(NamedTuple):
    first: Draft
    result: StyleResult


def first_phrase(text: str) -> str:
    return text.strip().split("\n", 1)[0][:FIRST_PHRASE_MAX_CHARS]


def sample_report(index: int, sample: Sample) -> dict[str, object]:
    chosen = sample.result.draft
    return {
        "sample": index,
        "chars": sum(len(text) for text in chosen.texts),
        "first_draft_chars": sum(len(text) for text in sample.first.texts),
        "used_facts": len(chosen.used_fact_ids),
        "first_draft_used_facts": len(sample.first.used_fact_ids),
        "writer_attempts_first_draft": sample.first.attempts,
        "regenerations": sample.result.regenerations,
        "regressions_rejected": sample.result.regressions_rejected,
        "chosen_attempt": sample.result.chosen_attempt,
        "regeneration_failed": sample.result.regeneration_failed,
        "length_violations": [v.issue.value for v in chosen.length_violations],
        "remaining_violations": [
            {"rule": v.rule.value, "excerpt": v.excerpt, "explanation": v.explanation}
            for v in sample.result.report.violations
        ],
        "unverified_numbers": chosen.unverified_numbers,
        "first_phrase": first_phrase(chosen.texts[0]),
        "text": chosen.texts[0],
        "first_draft_text": sample.first.texts[0],
    }


def save_report(slug: str, report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / OUTPUT_TEMPLATE.format(slug=slug)).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def count_requests(*clients: RecordingLLMClient) -> int:
    return sum(len(client.prompts) for client in clients)


def check_sample(sample: Sample, case: LongCase, limits: WritingLimits) -> None:
    draft = sample.result.draft
    known = {fact.id for fact in case.fact_set.facts}
    assert set(draft.used_fact_ids) <= known
    chars = sum(len(text) for text in draft.texts)
    shown_as_short = any(v.issue is LengthIssue.TOO_SHORT for v in draft.length_violations)
    assert chars >= limits.long_min_chars or shown_as_short
    assert chars <= limits.long_max_chars


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
@pytest.mark.parametrize("case", LONG_CASES, ids=[case.slug for case in LONG_CASES])
async def test_live_long_post_does_not_collapse(case: LongCase) -> None:
    settings: Settings = live_settings()
    writing_limits = WritingLimits.from_settings(settings)
    style_limits = StyleLimits.from_settings(settings)
    samples: list[Sample] = []
    async with build_http_client(settings) as http_client:
        factory = LLMClientFactory(settings, http_client)
        writer = RecordingLLMClient(factory.get_client(LLMStep.WRITING))
        critic = RecordingLLMClient(factory.get_client(LLMStep.STYLE_CRITIQUE))
        for _ in range(SAMPLES_PER_SET):
            first = await write_draft(
                as_client(writer), case.fact_set, PostFormat.LONG, writing_limits, NO_EXAMPLES
            )
            result = await review_style(
                as_client(writer),
                as_client(critic),
                first,
                case.fact_set,
                writing_limits,
                style_limits,
                NO_EXAMPLES,
            )
            samples.append(Sample(first, result))
    requests = count_requests(writer, critic)
    logger.info("live requests=%d", requests)

    report: dict[str, object] = {
        "fixture_note": FIXTURE_NOT_SOURCED,
        "topic": case.fact_set.topic,
        "format": PostFormat.LONG.value,
        "long_min_chars": writing_limits.long_min_chars,
        "long_min_used_facts": writing_limits.long_min_used_facts,
        "requests_total": requests,
        "requests_writer": len(writer.prompts),
        "requests_critic": len(critic.prompts),
        **fact_set_entry(case.fact_set),
        "samples": [sample_report(index, sample) for index, sample in enumerate(samples, start=1)],
    }
    save_report(case.slug, report)
    for sample in samples:
        check_sample(sample, case, writing_limits)
