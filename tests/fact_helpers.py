from app.domain.snippet import Snippet, SnippetOrigin
from app.services.facts import FactLimits

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
YOUTUBE = make_snippet(
    "https://m.youtube.com/watch?v=1",
    "Сражение состоялось 8 сентября 1380 года, рассказывает ведущий.",
)
BLOG = make_snippet(
    "https://User.LiveJournal.com/1.html",
    "Сражение состоялось 8 сентября 1380 года, пишет автор блога.",
)
FOURTH_SITE = make_snippet(
    "https://other.example.net/a", "Мамай потерпел поражение на Куликовом поле, пишут историки."
)
SNIPPETS: list[Snippet] = [RU_WIKI, EN_WIKI, HISTORY_SITE, NEWS_SITE, OTHER_SITE]
WEAK_SNIPPETS: list[Snippet] = [*SNIPPETS, YOUTUBE, BLOG]
FOURTH_DOMAIN_SNIPPETS: list[Snippet] = [*SNIPPETS, FOURTH_SITE]
WEAK_DOMAINS = ("youtube.com", "livejournal.com")

DATE_RU_QUOTE = "Куликовская битва произошла 8 сентября 1380 года"
DATE_EN_QUOTE = "The Battle of Kulikovo was fought on 8 September 1380"
DATE_SITE_QUOTE = "Сражение состоялось 8 сентября 1380 года"
ARMY_60_QUOTE = "Численность русского войска оценивают в 60 000 человек"
ARMY_150_QUOTE = "численность русского войска составляла 150 000 человек"
FOURTH_QUOTE = "Мамай потерпел поражение на Куликовом поле"
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
    weak_domains: tuple[str, ...] = (),
    max_per_domain: int | None = None,
    domain_cap_floor: int = 0,
) -> FactLimits:
    return FactLimits(
        input_max_chars=input_max_chars,
        min_quote_chars=min_quote_chars,
        max_facts=max_facts,
        min_facts=min_facts,
        domain_groups=domain_groups,
        weak_domains=weak_domains,
        max_per_domain=max_per_domain,
        domain_cap_floor=domain_cap_floor,
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
