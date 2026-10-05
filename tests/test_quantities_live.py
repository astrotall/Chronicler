import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from app.config.constants import FACT_CANDIDATES_MAX, FACT_EXTRACTION_MAX_TOKENS, LLMStep
from app.config.quantities import RUSSIAN_PHRASES
from app.config.settings import Settings
from app.domain.draft import PostFormat
from app.domain.fact import Fact, FactSet, FactStatus, SourceRef
from app.domain.snippet import Snippet
from app.llm.factory import LLMClientFactory, build_http_client
from app.prompts.fact_extraction import render_fact_extraction
from app.services.facts import (
    CandidateVerdict,
    ExtractedFact,
    ExtractedFacts,
    FactLimits,
    verify_candidate,
)
from app.services.generator import WritingLimits, verify_numbers, write_draft
from app.services.quantities import (
    clean_text,
    digit_shares,
    quantities_supported,
    times_quantities,
    word_quantities,
)
from app.services.quote_check import CANONICAL_DECIMAL_SEPARATOR, extract_numbers, numbers_supported
from app.services.short_post import split_sentences
from pydantic import BaseModel, ConfigDict, Field

from fact_snapshot import load_fact_set
from live_keys import LiveKeys, skip_without_key
from llm_helpers import RecordingLLMClient
from test_stance_live import RecordedResearch, shown_snippets

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
REPORT_FILE = "his43_quantities.json"
SAVED_SETS: dict[str, str] = {
    "kulikovo": "his8_kulikovo.json",
    "soviet_day_1930s": "his8_soviet_day_1930s.json",
}
RESEARCH_FILE = "his32_research_{slug}.json"
RECORDED_REPLY_FILES: tuple[str, ...] = (
    "his32_before_{slug}.json",
    "his32_after_{slug}.json",
    "his32_after2_{slug}.json",
)
REPLAY_SLUGS: tuple[str, ...] = ("feb30", "kulikovo", "soviet_day_1930s")
FRESH_SLUGS: tuple[str, ...] = ("kulikovo", "soviet_day_1930s")
SAVED_POST_KEYS: tuple[str, ...] = ("post", "thread")
FIXTURE_RUNS = 3
DROP_ALERT_RATIO = 0.05
LIVE_FORMAT = PostFormat.LONG
DECIMAL_POINT = CANONICAL_DECIMAL_SEPARATOR

pytestmark = [
    pytest.mark.integration,
    skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key),
]


def invented(fact_id: str, text: str) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(
                snippet_id="fixture",
                url="https://invented-fixture.example.org/nordland",
                domain="invented-fixture.example.org",
                quote=text,
            )
        ],
        status=FactStatus.SINGLE,
    )


INVENTED_SHARES_FIXTURE = FactSet(
    topic="Выборы в Сейм Нордланда 1931 года",
    facts=[
        invented(
            "F1", "На выборах 1931 года в Сейм Нордланда Партия плуга получила 37,4% голосов."
        ),
        invented("F2", "Партия моста на тех же выборах получила 14,3% голосов."),
        invented("F3", "Остальные голоса разделили партии центра и мелкие региональные списки."),
        invented("F4", "Явка на выборах 1931 года составила 84,1%."),
        invented("F5", "Сейм Нордланда состоял из 240 депутатов."),
        invented("F6", "После выборов Партия плуга и партии центра сформировали коалицию."),
        invented("F7", "Коалиция Партии плуга и партий центра распалась в 1933 году."),
        invented("F8", "Лидером Партии плуга был фермер Ханс Эрле."),
    ],
    disputes=[],
)


class RecordedReply(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    reply: dict[str, object]


class RecordedExtraction(BaseModel):
    extractor_replies: list[RecordedReply]


def quantity_rows(text: str) -> list[dict[str, object]]:
    cleaned = clean_text(text)
    found = [
        *((quantity, True) for quantity in word_quantities(cleaned, RUSSIAN_PHRASES)),
        *((quantity, False) for quantity in digit_shares(cleaned)),
        *((quantity, False) for quantity in times_quantities(cleaned)),
    ]
    return [
        {
            "form": quantity.form,
            "kind": quantity.kind.value,
            "value": str(quantity.value),
            "checked": checked,
        }
        for quantity, checked in sorted(found, key=lambda item: item[0].start)
    ]


def mentions(form: str, sentence: str) -> bool:
    if form.isdigit() or form.replace(DECIMAL_POINT, "", 1).isdigit():
        return form in extract_numbers(sentence)
    return form.casefold() in sentence.casefold()


def sentences_with(form: str, texts: Sequence[str]) -> list[str]:
    return [
        sentence
        for text in texts
        for sentence in split_sentences(clean_text(text))
        if mentions(form, sentence)
    ]


def post_report(texts: Sequence[str], fact_set: FactSet, tolerance: float) -> dict[str, object]:
    check = verify_numbers(texts, fact_set, tolerance)
    return {
        "texts": list(texts),
        "quantities": [row for text in texts for row in quantity_rows(text)],
        "unverified": check.unverified,
        "share_sets": check.share_sets,
        "flags": [
            {"form": form, "sentences": sentences_with(form, texts)} for form in check.unverified
        ],
    }


def fact_quantities(fact_set: FactSet) -> list[dict[str, object]]:
    return [
        {"id": fact.id, "text": fact.text, "quantities": rows}
        for fact in fact_set.facts
        if (rows := quantity_rows(fact.text))
    ]


def saved_posts(slug: str, fact_set: FactSet, tolerance: float) -> dict[str, object]:
    report = json.loads((OUTPUT_DIR / SAVED_SETS[slug]).read_text(encoding="utf-8"))
    return {
        key: post_report(report[key]["rendered"], fact_set, tolerance)
        for key in SAVED_POST_KEYS
        if report.get(key)
    }


def replay_reply(
    candidates: Sequence[ExtractedFact], snippets: dict[str, Snippet], limits: FactLimits
) -> dict[str, object]:
    candidates = list(candidates)[:FACT_CANDIDATES_MAX]
    quoted = 0
    dropped_by_quantity: list[dict[str, object]] = []
    with_quantities: list[dict[str, object]] = []
    for candidate in candidates:
        check = verify_candidate(candidate, snippets, limits)
        if not check.support:
            continue
        quoted += 1
        quotes = [ref.quote for ref in check.support]
        rows = quantity_rows(candidate.text)
        if any(row["checked"] for row in rows):
            with_quantities.append(
                {"text": candidate.text, "quantities": rows, "verdict": check.verdict.value}
            )
        if (
            check.verdict is CandidateVerdict.NUMBER_MISMATCH
            and numbers_supported(candidate.text, quotes)
            and not quantities_supported(candidate.text, quotes, limits.quantity_tolerance)
        ):
            dropped_by_quantity.append(
                {
                    "text": candidate.text,
                    "quotes": quotes,
                    "fact_quantities": rows,
                    "quote_quantities": [row for quote in quotes for row in quantity_rows(quote)],
                }
            )
    return {
        "candidates": len(candidates),
        "with_verified_quotes": quoted,
        "facts_with_word_quantities": with_quantities,
        "dropped_by_quantity_only": dropped_by_quantity,
        "drop_ratio": len(dropped_by_quantity) / quoted if quoted else 0.0,
    }


def recorded_replies(slug: str) -> list[tuple[str, ExtractedFacts]]:
    found: list[tuple[str, ExtractedFacts]] = []
    for template in RECORDED_REPLY_FILES:
        path = OUTPUT_DIR / template.format(slug=slug)
        if not path.exists():
            continue
        recorded = RecordedExtraction.model_validate_json(path.read_text(encoding="utf-8"))
        for reply in recorded.extractor_replies:
            if reply.schema_name == ExtractedFacts.__name__:
                found.append((path.name, ExtractedFacts.model_validate(reply.reply)))
    return found


def research_of(slug: str) -> RecordedResearch:
    path = OUTPUT_DIR / RESEARCH_FILE.format(slug=slug)
    return RecordedResearch.model_validate_json(path.read_text(encoding="utf-8"))


def write_report(report: dict[str, object]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / REPORT_FILE).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def test_live_quantities() -> None:
    settings = Settings()
    fact_limits = FactLimits.from_settings(settings)
    writing_limits = WritingLimits.from_settings(settings)
    tolerance = writing_limits.quantity_tolerance
    report: dict[str, object] = {"tolerance": tolerance}

    saved: dict[str, object] = {}
    saved_sets: dict[str, FactSet] = {}
    for slug, name in SAVED_SETS.items():
        fact_set = load_fact_set(OUTPUT_DIR / name)
        saved_sets[slug] = fact_set
        saved[slug] = {
            "facts": fact_quantities(fact_set),
            "posts": saved_posts(slug, fact_set, tolerance),
        }
    report["saved_his8"] = saved

    replays: list[dict[str, object]] = []
    for slug in REPLAY_SLUGS:
        snippets = shown_snippets(research_of(slug).research.snippets, fact_limits)
        for source, reply in recorded_replies(slug):
            replays.append(
                {"slug": slug, "source": source, **replay_reply(reply.facts, snippets, fact_limits)}
            )

    extraction_calls = 0
    writer_calls = 0
    async with build_http_client(settings) as llm_http:
        factory = LLMClientFactory(settings, llm_http)
        extractor = RecordingLLMClient(factory.get_client(LLMStep.FACT_EXTRACTION))
        for slug in FRESH_SLUGS:
            research = research_of(slug)
            snippets = shown_snippets(research.research.snippets, fact_limits)
            reply = await extractor.complete_json(
                render_fact_extraction(
                    research.topic, list(snippets.items()), fact_limits.min_quote_chars
                ),
                ExtractedFacts,
                max_tokens=FACT_EXTRACTION_MAX_TOKENS,
            )
            replays.append(
                {
                    "slug": slug,
                    "source": "fresh",
                    **replay_reply(reply.facts, snippets, fact_limits),
                }
            )
        extraction_calls = len(extractor.prompts)

        writer = RecordingLLMClient(factory.get_client(LLMStep.WRITING))
        fixture_runs: list[dict[str, object]] = []
        for _ in range(FIXTURE_RUNS):
            draft = await write_draft(writer, INVENTED_SHARES_FIXTURE, LIVE_FORMAT, writing_limits)
            fixture_runs.append(
                {
                    "attempts": draft.attempts,
                    **post_report(draft.texts, INVENTED_SHARES_FIXTURE, tolerance),
                }
            )
        saved_runs: dict[str, object] = {}
        for slug, fact_set in saved_sets.items():
            draft = await write_draft(writer, fact_set, LIVE_FORMAT, writing_limits)
            saved_runs[slug] = {
                "attempts": draft.attempts,
                **post_report(draft.texts, fact_set, tolerance),
            }
        writer_calls = len(writer.prompts)

    report["fact_step_replays"] = replays
    report["fixture"] = {
        "facts": fact_quantities(INVENTED_SHARES_FIXTURE),
        "runs": fixture_runs,
    }
    report["his8_rewrites"] = saved_runs
    report["deepseek_calls"] = {
        "fact_extraction": extraction_calls,
        "writing": writer_calls,
        "total": extraction_calls + writer_calls,
    }
    write_report(report)

    assert extraction_calls == len(FRESH_SLUGS)
    assert len(fixture_runs) == FIXTURE_RUNS
