from collections.abc import Sequence

from app.domain.llm import LLMResult, Message
from app.domain.snippet import Snippet, SnippetOrigin
from app.llm.client import LLMClient
from app.llm.errors import LLMInvalidResponseError
from app.services.facts import FactLimits
from pydantic import BaseModel, ValidationError

from research_helpers import make_snippet

RU_WIKI = make_snippet(
    "https://ru.wikipedia.org/wiki/Куликовская_битва",
    "Куликовская битва произошла 8 сентября 1380 года на Куликовом поле. "
    "Войско Дмитрия Донского разбило войско Мамая.",
    SnippetOrigin.WIKIPEDIA_RU,
    "Куликовская битва",
)
EN_WIKI = make_snippet(
    "https://en.wikipedia.org/wiki/Battle_of_Kulikovo",
    "The Battle of Kulikovo was fought on 8 September 1380 between the Grand Duchy of "
    "Moscow and the Golden Horde led by Mamai.",
    SnippetOrigin.WIKIPEDIA_EN,
    "Battle of Kulikovo",
)
HISTORY_SITE = make_snippet(
    "https://www.history.example.org/kulikovo",
    "Сражение состоялось 8 сентября 1380 года у впадения Непрядвы в Дон. [...] "
    "Численность русского войска оценивают в 60 000 человек.",
)
NEWS_SITE = make_snippet(
    "https://news.example.org/kulikovo",
    "Сражение состоялось 8 сентября 1380 года, сообщает летопись.",
)
OTHER_SITE = make_snippet(
    "https://hronos.example.com/kulikovo",
    "По другим оценкам, численность русского войска составляла 150 000 человек.",
)
SNIPPETS: list[Snippet] = [RU_WIKI, EN_WIKI, HISTORY_SITE, NEWS_SITE, OTHER_SITE]

DATE_RU_QUOTE = "Куликовская битва произошла 8 сентября 1380 года"
DATE_EN_QUOTE = "The Battle of Kulikovo was fought on 8 September 1380"
DATE_SITE_QUOTE = "Сражение состоялось 8 сентября 1380 года"
ARMY_60_QUOTE = "Численность русского войска оценивают в 60 000 человек"
ARMY_150_QUOTE = "численность русского войска составляла 150 000 человек"
WINNER_QUOTE = "Войско Дмитрия Донского разбило войско Мамая"


def make_limits(
    *,
    input_max_chars: int = 60000,
    min_quote_chars: int = 20,
    max_facts: int = 20,
    min_facts: int = 1,
    domain_groups: tuple[tuple[str, ...], ...] = (
        ("wikipedia.org", "wikimedia.org", "ruwiki.ru", "wikiwand.com"),
    ),
) -> FactLimits:
    return FactLimits(
        input_max_chars=input_max_chars,
        min_quote_chars=min_quote_chars,
        max_facts=max_facts,
        min_facts=min_facts,
        domain_groups=domain_groups,
    )


def support(snippet: str, quote: str) -> dict[str, str]:
    return {"snippet": snippet, "quote": quote}


def fact(text: str, *items: dict[str, str]) -> dict[str, object]:
    return {"text": text, "support": list(items)}


def extraction(*facts: dict[str, object]) -> dict[str, object]:
    return {"facts": list(facts)}


def conflict(
    *fact_ids: str, explanation: str = "Источники расходятся", contradiction: bool = True
) -> dict[str, object]:
    return {"fact_ids": list(fact_ids), "explanation": explanation, "contradiction": contradiction}


def conflicts(*items: dict[str, object]) -> dict[str, object]:
    return {"conflicts": list(items)}


NO_CONFLICTS = conflicts()


class ScriptedLLMClient:
    def __init__(self, *replies: object) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[list[Message], type[BaseModel], int]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> LLMResult:
        raise NotImplementedError

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int,
    ) -> T:
        self.calls.append((list(messages), schema, max_tokens))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        try:
            return schema.model_validate(reply)
        except ValidationError as error:
            raise LLMInvalidResponseError("invalid json reply") from error


def as_client(fake: ScriptedLLMClient) -> LLMClient:
    return fake
