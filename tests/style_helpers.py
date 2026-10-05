from collections.abc import Sequence

from app.domain.draft import Draft, DraftPart, LengthViolation, PostFormat
from app.domain.fact import Dispute, Fact, FactSet, FactStatus, SourceRef
from app.services.generator import WritingLimits
from app.services.style_review import StyleLimits

SOURCE_URL = "https://unique-style-source.example.org/kulikovo"
UNIQUE_QUOTE = "UNIQUE-STYLE-QUOTE: verbatim source text"
UNIQUE_URL_MARK = "unique-style-source"


def style_fact(fact_id: str, text: str, status: FactStatus = FactStatus.SINGLE) -> Fact:
    return Fact(
        id=fact_id,
        text=text,
        support=[
            SourceRef(snippet_id="abc", url=SOURCE_URL, domain="example.org", quote=UNIQUE_QUOTE)
        ],
        status=status,
    )


FACT_SET = FactSet(
    topic="Куликовская битва",
    facts=[
        style_fact("F1", "Куликовская битва произошла 8 сентября 1380 года.", FactStatus.CONFIRMED),
        style_fact("F2", "Сражение произошло на Куликовом поле, у впадения Непрядвы в Дон."),
        style_fact("F3", "Русским войском командовал великий князь Дмитрий Иванович."),
        style_fact(
            "F4", "Перед сражением состоялся поединок инока Пересвета с богатырём Челубеем."
        ),
        style_fact(
            "F5", "Численность русского войска оценивают в 60 000 человек.", FactStatus.DISPUTED
        ),
        style_fact(
            "F6", "Численность русского войска составляла 150 000 человек.", FactStatus.DISPUTED
        ),
    ],
    disputes=[Dispute(fact_ids=["F5", "F6"], explanation="Оценки численности войска расходятся.")],
)


def make_draft(
    texts: Sequence[str],
    post_format: PostFormat | None = None,
    *,
    unverified_numbers: Sequence[str] = (),
    unverified_share_sets: Sequence[Sequence[str]] = (),
    length_violations: Sequence[LengthViolation] = (),
    used_fact_ids: Sequence[str] = ("F1",),
) -> Draft:
    chosen = post_format or (PostFormat.SHORT if len(texts) == 1 else PostFormat.THREAD)
    return Draft(
        post_format=chosen,
        parts=[DraftPart(text=text) for text in texts],
        used_fact_ids=list(used_fact_ids),
        unverified_numbers=list(unverified_numbers),
        unverified_share_sets=[list(share_set) for share_set in unverified_share_sets],
        length_violations=list(length_violations),
        attempts=1,
    )


def make_writing_limits() -> WritingLimits:
    return WritingLimits(
        short_max_chars=280,
        long_max_chars=25000,
        thread_tweet_max_chars=280,
        thread_max_tweets=12,
    )


def make_style_limits(
    *,
    critic_enabled: bool = True,
    max_regenerations: int = 2,
    critic_max_findings: int = 10,
    min_retained_chars_ratio: float = 0.6,
    min_retained_facts_ratio: float = 0.6,
    fragment_overlap: float = 0.75,
    drop_surviving_claims: bool = False,
) -> StyleLimits:
    return StyleLimits(
        critic_enabled=critic_enabled,
        max_regenerations=max_regenerations,
        critic_max_findings=critic_max_findings,
        min_retained_chars_ratio=min_retained_chars_ratio,
        min_retained_facts_ratio=min_retained_facts_ratio,
        fragment_overlap=fragment_overlap,
        drop_surviving_claims=drop_surviving_claims,
    )


def finding(
    excerpt: str, rule: str, explanation: str = "Пояснение.", violation: bool = True
) -> dict[str, object]:
    return {"excerpt": excerpt, "rule": rule, "explanation": explanation, "violation": violation}


def findings(*items: dict[str, object]) -> dict[str, object]:
    return {"findings": list(items)}


NO_FINDINGS = findings()


def single_reply(text: str, *fact_ids: str) -> dict[str, object]:
    return {"fact_ids": list(fact_ids) or ["F1"], "text": text}


def thread_reply(*tweets: str) -> dict[str, object]:
    return {"fact_ids": ["F1"], "tweets": list(tweets)}
