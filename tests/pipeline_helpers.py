import asyncio
from collections.abc import Sequence
from pathlib import Path

from app.domain.draft import PostFormat
from app.domain.llm import Message
from app.domain.pipeline import PipelineStage
from app.domain.snippet import Snippet, SnippetOrigin
from app.research.errors import SourceError
from app.research.source import ResearchSource
from app.services.facts import FactLimits
from app.services.generator import WritingLimits
from app.services.pipeline import Pipeline, PipelineClients, PipelineLimits
from app.services.research import ResearchLimits, ResearchService
from app.services.run_store import InMemoryRunStore, RunStore
from app.services.style_review import StyleLimits
from pydantic import BaseModel

from llm_helpers import ScriptedLLMClient, as_client
from research_helpers import make_snippet

TOPIC = "Куликовская битва"
DATE_QUOTE = "Куликовская битва произошла 8 сентября 1380 года"
DATE_SITE_QUOTE = "Сражение состоялось 8 сентября 1380 года"
COMMANDER_QUOTE = "Русским войском командовал великий князь Дмитрий Иванович"
DUEL_QUOTE = "Перед сражением состоялся поединок инока Пересвета с Челубеем"
ALLY_QUOTE = "Мамай рассчитывал на помощь литовского князя Ягайло"
NICKNAME_QUOTE = "После победы Дмитрий получил прозвище Донской"
ARMY_60_QUOTE = "Численность русского войска оценивают в 60 000 человек"
ARMY_150_QUOTE = "численность русского войска составляла 150 000 человек"

WIKI = make_snippet(
    "https://ru.wikipedia.org/wiki/Куликовская_битва",
    f"{DATE_QUOTE} на Куликовом поле. {COMMANDER_QUOTE}. {DUEL_QUOTE}. {ALLY_QUOTE}. "
    f"{NICKNAME_QUOTE}. {ARMY_60_QUOTE}.",
    SnippetOrigin.WIKIPEDIA_RU,
    "Куликовская битва",
)
SITE = make_snippet(
    "https://history.example.org/kulikovo?a=1&b=2",
    f"{DATE_SITE_QUOTE} у впадения Непрядвы в Дон. [...] По другим оценкам, {ARMY_150_QUOTE}.",
)
SNIPPETS: list[Snippet] = [WIKI, SITE]

DATE_FACT = "Куликовская битва произошла 8 сентября 1380 года."
COMMANDER_FACT = "Русским войском командовал великий князь Дмитрий Иванович."
DUEL_FACT = "Перед сражением состоялся поединок инока Пересвета с Челубеем."
ALLY_FACT = "Мамай рассчитывал на помощь литовского князя Ягайло."
NICKNAME_FACT = "После победы Дмитрий получил прозвище Донской."
ARMY_60_FACT = "Численность русского войска оценивают в 60 000 человек."
ARMY_150_FACT = "Численность русского войска составляла 150 000 человек."
DISPUTE_EXPLANATION = "Оценки численности русского войска расходятся."

SHORT_TEXT = (
    "8 сентября 1380 года на Куликовом поле сошлись два войска. "
    "Русских вёл великий князь Дмитрий Иванович."
)
SHORTER_TEXT = "8 сентября 1380 года русских вёл великий князь Дмитрий Иванович."
THREAD_TWEETS = [
    "8 сентября 1380 года русское войско вышло на Куликово поле.",
    "Перед сражением инок Пересвет бился с Челубеем.",
    "Сколько было русских, источники расходятся: по одним данным 60 000, по другим 150 000.",
]
CLEAN_CRITIC: dict[str, object] = {"findings": []}
QUERIES: dict[str, object] = {
    "queries": ["Куликовская битва", "Battle of Kulikovo", "Куликовская битва численность"]
}


def support(label: str, quote: str) -> dict[str, str]:
    return {"snippet": label, "quote": quote}


def fact(text: str, *items: dict[str, str]) -> dict[str, object]:
    return {"text": text, "support": list(items)}


FULL_EXTRACTION: dict[str, object] = {
    "facts": [
        fact(DATE_FACT, support("S1", DATE_QUOTE), support("S2", DATE_SITE_QUOTE)),
        fact(COMMANDER_FACT, support("S1", COMMANDER_QUOTE)),
        fact(DUEL_FACT, support("S1", DUEL_QUOTE)),
        fact(ALLY_FACT, support("S1", ALLY_QUOTE)),
        fact(NICKNAME_FACT, support("S1", NICKNAME_QUOTE)),
        fact(ARMY_60_FACT, support("S1", ARMY_60_QUOTE)),
        fact(ARMY_150_FACT, support("S2", ARMY_150_QUOTE)),
    ]
}
POOR_EXTRACTION: dict[str, object] = {
    "facts": [
        fact(DATE_FACT, support("S1", DATE_QUOTE)),
        fact(ARMY_60_FACT, support("S1", ARMY_60_QUOTE)),
        fact(ARMY_150_FACT, support("S2", ARMY_150_QUOTE)),
    ]
}
ARMY_CONFLICT: dict[str, object] = {
    "conflicts": [
        {"fact_ids": ["C6", "C7"], "explanation": DISPUTE_EXPLANATION, "contradiction": True}
    ]
}
POOR_CONFLICT: dict[str, object] = {
    "conflicts": [
        {"fact_ids": ["C2", "C3"], "explanation": DISPUTE_EXPLANATION, "contradiction": True}
    ]
}


def short_reply(text: str = SHORT_TEXT, fact_ids: Sequence[str] = ("F1", "F2")) -> object:
    return {"fact_ids": list(fact_ids), "text": text}


def thread_reply(
    tweets: Sequence[str] = THREAD_TWEETS, fact_ids: Sequence[str] = ("F1", "F3")
) -> object:
    return {"fact_ids": list(fact_ids), "tweets": list(tweets)}


class FakeSource:
    def __init__(
        self,
        name: str,
        snippets: Sequence[Snippet] = (),
        error: SourceError | None = None,
    ) -> None:
        self.name = name
        self._snippets = list(snippets)
        self._error = error
        self.calls = 0

    async def search(self, query: str) -> list[Snippet]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._snippets)


class GatedLLMClient(ScriptedLLMClient):
    def __init__(self, *replies: object) -> None:
        super().__init__(*replies)
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        self.entered.set()
        await self.gate.wait()
        return await super().complete_json(
            messages, schema, temperature=temperature, max_tokens=max_tokens
        )


class Clients:
    def __init__(
        self,
        planner: ScriptedLLMClient | None = None,
        extractor: ScriptedLLMClient | None = None,
        writer: ScriptedLLMClient | None = None,
        critic: ScriptedLLMClient | None = None,
    ) -> None:
        self.planner = planner or ScriptedLLMClient(QUERIES)
        self.extractor = extractor or ScriptedLLMClient(FULL_EXTRACTION, ARMY_CONFLICT)
        self.writer = writer or ScriptedLLMClient(short_reply())
        self.critic = critic or ScriptedLLMClient(CLEAN_CRITIC)

    def bundle(self) -> PipelineClients:
        return PipelineClients(
            planner=as_client(self.planner),
            extractor=as_client(self.extractor),
            writer=as_client(self.writer),
            critic=as_client(self.critic),
        )


def make_limits(
    *,
    thread_min_facts: int = 5,
    default_format: PostFormat = PostFormat.SHORT,
    examples_dir: Path | None = None,
    critic_enabled: bool = True,
) -> PipelineLimits:
    return PipelineLimits(
        facts=FactLimits(
            input_max_chars=60000,
            min_quote_chars=20,
            max_facts=20,
            min_facts=3,
            domain_groups=(("wikipedia.org", "wikimedia.org", "ruwiki.ru", "wikiwand.com"),),
        ),
        writing=WritingLimits(
            short_max_chars=280,
            long_max_chars=25000,
            thread_tweet_max_chars=280,
            thread_max_tweets=12,
        ),
        style=StyleLimits(
            critic_enabled=critic_enabled, max_regenerations=2, critic_max_findings=10
        ),
        examples_dir=examples_dir or Path("missing-examples-dir"),
        examples_max=3,
        thread_min_facts=thread_min_facts,
        default_format=default_format,
    )


def research_service(sources: Sequence[ResearchSource]) -> ResearchService:
    return ResearchService(sources, ResearchLimits(max_concurrency=5, snippet_max_chars=8000))


def make_pipeline(
    clients: Clients,
    sources: Sequence[ResearchSource] | None = None,
    *,
    store: RunStore | None = None,
    limits: PipelineLimits | None = None,
) -> Pipeline:
    chosen = list(sources) if sources is not None else [FakeSource("wiki", SNIPPETS)]
    return Pipeline(
        clients.bundle(),
        research_service(chosen),
        store or InMemoryRunStore(20),
        limits or make_limits(),
    )


class StageLog:
    def __init__(self) -> None:
        self.stages: list[PipelineStage] = []

    async def __call__(self, stage: PipelineStage) -> None:
        self.stages.append(stage)


def prompt_text(client: ScriptedLLMClient, call: int) -> str:
    messages, _, _ = client.calls[call]
    return "\n".join(message.content for message in messages)
