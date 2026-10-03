import json
import logging
from pathlib import Path

import pytest
from app.config.constants import LLMProvider, LLMStep
from app.config.settings import Settings
from app.domain.draft import Draft, PostFormat
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.llm.factory import LLMClientFactory, build_http_client
from app.services.generator import WritingLimits, write_draft
from app.services.style import load_examples

from live_keys import LiveKeys, skip_without_key

KEYS = LiveKeys()
OUTPUT_PATH = Path("data/comparisons/generator_live.json")
RU_URL = "https://ru.wikipedia.org/wiki/Куликовская_битва"
EN_URL = "https://en.wikipedia.org/wiki/Battle_of_Kulikovo"

logger = logging.getLogger(__name__)


def live_fact(fact_id: str, text: str, status: FactStatus = FactStatus.SINGLE) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="live", url=RU_URL, domain="wikipedia.org", quote=text),
            SourceRef(snippet_id="live-en", url=EN_URL, domain="wikipedia.org", quote=text),
        ],
        status=status,
    )


FACT_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        live_fact("F1", "Куликовская битва произошла 8 сентября 1380 года.", FactStatus.CONFIRMED),
        live_fact(
            "F2",
            "Сражение произошло на Куликовом поле, у впадения реки Непрядвы в Дон.",
            FactStatus.CONFIRMED,
        ),
        live_fact("F3", "Русским войском командовал великий князь московский Дмитрий Иванович."),
        live_fact(
            "F4",
            "Ордынским войском командовал беклярбек Мамай, который не был Чингизидом и правил "
            "от имени ханов, возведённых им на престол.",
        ),
        live_fact("F5", "После победы Дмитрий Иванович получил прозвище Донской."),
        live_fact(
            "F6",
            "Мамай рассчитывал соединиться с войском литовского князя Ягайло, но тот не успел "
            "к битве.",
        ),
        live_fact("F7", "В 1382 году хан Тохтамыш взял и сжёг Москву."),
        live_fact(
            "F8",
            "Перед сражением состоялся поединок инока Пересвета с ордынским богатырём Челубеем.",
        ),
        live_fact(
            "F9",
            "Численность русского войска оценивают в 60 000 человек.",
            FactStatus.DISPUTED,
        ),
        live_fact(
            "F10",
            "Численность русского войска составляла 150 000 человек.",
            FactStatus.DISPUTED,
        ),
    ],
    disputes=[
        Dispute(
            fact_ids=["F9", "F10"],
            explanation="Источники называют разную численность русского войска.",
        )
    ],
)


def check_structure(draft: Draft, limits: WritingLimits) -> None:
    known = {fact.id for fact in FACT_SET.facts}
    assert draft.texts
    assert all(text.strip() for text in draft.texts)
    assert draft.used_fact_ids
    assert set(draft.used_fact_ids) <= known
    limit = limits.part_max_chars(draft.post_format)
    assert all(len(rendered) <= limit for rendered in draft.rendered)


def save_drafts(drafts: list[Draft]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            [draft.model_dump(mode="json") for draft in drafts], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
async def test_live_writing_of_a_short_post_and_a_thread() -> None:
    settings = Settings(
        telegram_bot_token="live-test",
        owner_telegram_ids=[1],
        deepseek_api_key=KEYS.deepseek_api_key,
        llm_query_planning_provider=LLMProvider.DEEPSEEK,
        llm_fact_extraction_provider=LLMProvider.DEEPSEEK,
        llm_writing_provider=LLMProvider.DEEPSEEK,
        llm_style_critique_provider=LLMProvider.DEEPSEEK,
    )
    limits = WritingLimits.from_settings(settings)
    examples = load_examples(settings.examples_dir, settings.examples_max)
    async with build_http_client(settings) as http_client:
        client = LLMClientFactory(settings, http_client).get_client(LLMStep.WRITING)
        short = await write_draft(client, FACT_SET, PostFormat.SHORT, limits, examples)
        thread = await write_draft(client, FACT_SET, PostFormat.THREAD, limits, examples)

    save_drafts([short, thread])
    for draft in (short, thread):
        logger.info(
            "live draft format=%s parts=%d used=%s unverified=%d violations=%d attempts=%d",
            draft.post_format,
            len(draft.parts),
            draft.used_fact_ids,
            len(draft.unverified_numbers),
            len(draft.length_violations),
            draft.attempts,
        )
        check_structure(draft, limits)
