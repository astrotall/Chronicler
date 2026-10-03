import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from app.config.constants import LLMProvider, LLMStep
from app.config.settings import Settings
from app.domain.draft import Draft, PostFormat
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.domain.llm import Role
from app.llm.factory import LLMClientFactory, build_http_client
from app.services.generator import WritingLimits, write_draft
from app.services.style import load_examples

from live_keys import LiveKeys, skip_without_key
from llm_helpers import RecordingLLMClient, as_client

KEYS = LiveKeys()
OUTPUT_DIR = Path("data/comparisons")
OUTPUT_TEMPLATE = "generator_live_{slug}.json"
EXAMPLES_OUTPUT_TEMPLATE = "generator_live_examples_{slug}.json"
SAMPLES: dict[PostFormat, int] = {PostFormat.SHORT: 3, PostFormat.THREAD: 2}
EXAMPLES_SAMPLES: dict[PostFormat, int] = {PostFormat.SHORT: 2, PostFormat.THREAD: 2}
NO_EXAMPLES: tuple[str, ...] = ()

logger = logging.getLogger(__name__)


class SourcePair(NamedTuple):
    ru_url: str
    en_url: str


class LiveCase(NamedTuple):
    slug: str
    fact_set: FactSet


KULIKOVO = SourcePair(
    ru_url="https://ru.wikipedia.org/wiki/Куликовская_битва",
    en_url="https://en.wikipedia.org/wiki/Battle_of_Kulikovo",
)
APOLLO = SourcePair(
    ru_url="https://ru.wikipedia.org/wiki/Аполлон-11",
    en_url="https://en.wikipedia.org/wiki/Apollo_11",
)


def live_fact(
    fact_id: str, text: str, sources: SourcePair, status: FactStatus = FactStatus.SINGLE
) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="live", url=sources.ru_url, domain="wikipedia.org", quote=text),
            SourceRef(snippet_id="live-en", url=sources.en_url, domain="wikipedia.org", quote=text),
        ],
        status=status,
    )


KULIKOVO_FACTS = FactSet(
    topic="Куликовская битва",
    facts=[
        live_fact(
            "F1",
            "Куликовская битва произошла 8 сентября 1380 года.",
            KULIKOVO,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F2",
            "Сражение произошло на Куликовом поле, у впадения реки Непрядвы в Дон.",
            KULIKOVO,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F3",
            "Русским войском командовал великий князь московский Дмитрий Иванович.",
            KULIKOVO,
        ),
        live_fact(
            "F4",
            "Ордынским войском командовал беклярбек Мамай, который не был Чингизидом и правил "
            "от имени ханов, возведённых им на престол.",
            KULIKOVO,
        ),
        live_fact("F5", "После победы Дмитрий Иванович получил прозвище Донской.", KULIKOVO),
        live_fact(
            "F6",
            "Мамай рассчитывал соединиться с войском литовского князя Ягайло, но тот не успел "
            "к битве.",
            KULIKOVO,
        ),
        live_fact("F7", "В 1382 году хан Тохтамыш взял и сжёг Москву.", KULIKOVO),
        live_fact(
            "F8",
            "Перед сражением состоялся поединок инока Пересвета с ордынским богатырём Челубеем.",
            KULIKOVO,
        ),
        live_fact(
            "F9",
            "Численность русского войска оценивают в 60 000 человек.",
            KULIKOVO,
            FactStatus.DISPUTED,
        ),
        live_fact(
            "F10",
            "Численность русского войска составляла 150 000 человек.",
            KULIKOVO,
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

APOLLO_FACTS = FactSet(
    topic="Высадка «Аполлона-11» на Луну",
    facts=[
        live_fact(
            "F1",
            "Лунный модуль «Игл» экспедиции «Аполлон-11» сел на Луну 20 июля 1969 года.",
            APOLLO,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F2",
            "Экипаж «Аполлона-11» составляли Нил Армстронг, Базз Олдрин и Майкл Коллинз. "
            "Коллинз оставался на окололунной орбите в командном модуле «Колумбия».",
            APOLLO,
            FactStatus.CONFIRMED,
        ),
        live_fact(
            "F3",
            "Армстронг первым ступил на поверхность Луны, Олдрин вышел вслед за ним примерно "
            "через 19 минут.",
            APOLLO,
        ),
        live_fact(
            "F4",
            "Во время посадки бортовой компьютер несколько раз подавал сигналы ошибок 1202 "
            "и 1201 из-за перегрузки.",
            APOLLO,
        ),
        live_fact(
            "F5",
            "Армстронг перешёл на ручное управление и увёл модуль от каменистого кратера, "
            "к которому его вела автоматика.",
            APOLLO,
        ),
        live_fact(
            "F6",
            "Выход Армстронга и Олдрина на поверхность Луны длился около 2 часов 31 минуты.",
            APOLLO,
        ),
        live_fact(
            "F7",
            "По данным, которые звучали во время полёта, при посадке топлива оставалось "
            "примерно на 25 секунд.",
            APOLLO,
            FactStatus.DISPUTED,
        ),
        live_fact(
            "F8",
            "Послеполётный анализ показал, что при посадке топлива оставалось примерно "
            "на 45 секунд.",
            APOLLO,
            FactStatus.DISPUTED,
        ),
    ],
    disputes=[
        Dispute(
            fact_ids=["F7", "F8"],
            explanation="Источники по-разному оценивают остаток топлива в момент посадки.",
        )
    ],
)

LIVE_CASES = [
    LiveCase(slug="kulikovo", fact_set=KULIKOVO_FACTS),
    LiveCase(slug="apollo11", fact_set=APOLLO_FACTS),
]


def live_settings() -> Settings:
    return Settings(
        telegram_bot_token="live-test",
        owner_telegram_ids=[1],
        deepseek_api_key=KEYS.deepseek_api_key,
        llm_query_planning_provider=LLMProvider.DEEPSEEK,
        llm_fact_extraction_provider=LLMProvider.DEEPSEEK,
        llm_writing_provider=LLMProvider.DEEPSEEK,
        llm_style_critique_provider=LLMProvider.DEEPSEEK,
    )


def check_structure(draft: Draft, fact_set: FactSet, limits: WritingLimits) -> None:
    known = {fact.id for fact in fact_set.facts}
    assert draft.texts
    assert all(text.strip() for text in draft.texts)
    assert draft.used_fact_ids
    assert set(draft.used_fact_ids) <= known
    limit = limits.part_max_chars(draft.post_format)
    assert all(len(rendered) <= limit for rendered in draft.rendered)
    assert draft.length_violations == []
    assert draft.dropped_tail == []


def system_message_counts(client: RecordingLLMClient) -> set[int]:
    return {
        sum(1 for message in prompt if message.role is Role.SYSTEM) for prompt in client.prompts
    }


async def write_samples(
    settings: Settings,
    fact_set: FactSet,
    limits: WritingLimits,
    examples: Sequence[str],
    samples: dict[PostFormat, int],
) -> tuple[RecordingLLMClient, list[Draft]]:
    drafts: list[Draft] = []
    async with build_http_client(settings) as http_client:
        client = RecordingLLMClient(
            LLMClientFactory(settings, http_client).get_client(LLMStep.WRITING)
        )
        for post_format, count in samples.items():
            for _ in range(count):
                drafts.append(
                    await write_draft(as_client(client), fact_set, post_format, limits, examples)
                )
    logger.info("live requests=%d", len(client.prompts))
    return client, drafts


def save_drafts(path: Path, drafts: list[Draft]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [draft.model_dump(mode="json") for draft in drafts], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def log_draft(draft: Draft) -> None:
    logger.info(
        "live draft format=%s parts=%d chars=%s used=%s unverified=%d violations=%d "
        "attempts=%d dropped_tail=%d",
        draft.post_format,
        len(draft.parts),
        [len(rendered) for rendered in draft.rendered],
        draft.used_fact_ids,
        len(draft.unverified_numbers),
        len(draft.length_violations),
        draft.attempts,
        len(draft.dropped_tail),
    )


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
@pytest.mark.parametrize("case", LIVE_CASES, ids=[case.slug for case in LIVE_CASES])
async def test_live_writing_of_a_short_post_and_a_thread(case: LiveCase) -> None:
    settings = live_settings()
    limits = WritingLimits.from_settings(settings)
    assert not limits.short_drop_tail
    client, drafts = await write_samples(settings, case.fact_set, limits, NO_EXAMPLES, SAMPLES)

    save_drafts(OUTPUT_DIR / OUTPUT_TEMPLATE.format(slug=case.slug), drafts)
    assert system_message_counts(client) == {1}
    for draft in drafts:
        log_draft(draft)
        check_structure(draft, case.fact_set, limits)


@pytest.mark.integration
@skip_without_key("DEEPSEEK_API_KEY", KEYS.deepseek_api_key)
@pytest.mark.parametrize("case", LIVE_CASES, ids=[case.slug for case in LIVE_CASES])
async def test_live_writing_with_the_author_examples(case: LiveCase) -> None:
    settings = live_settings()
    examples = load_examples(settings.examples_dir, settings.examples_max)
    if not examples:
        pytest.skip(f"no examples in {settings.examples_dir}")
    limits = WritingLimits.from_settings(settings)
    assert not limits.short_drop_tail
    client, drafts = await write_samples(
        settings, case.fact_set, limits, examples, EXAMPLES_SAMPLES
    )

    save_drafts(OUTPUT_DIR / EXAMPLES_OUTPUT_TEMPLATE.format(slug=case.slug), drafts)
    assert system_message_counts(client) == {2}
    for draft in drafts:
        log_draft(draft)
        check_structure(draft, case.fact_set, limits)
