import logging
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config.constants import (
    STYLE_CRITIC_EXCERPT_MAX_CHARS,
    STYLE_CRITIC_EXCERPT_MIN_CHARS,
    STYLE_CRITIC_EXPLANATION_MAX_CHARS,
    STYLE_CRITIC_MAX_TOKENS,
)
from app.domain.fact import FactSet
from app.domain.style import StyleRule, Violation, ViolationSource
from app.llm.client import LLMClient
from app.prompts.style_critique import render_critique
from app.services.quote_check import QuoteCheck, check_quote, text_segments

logger = logging.getLogger(__name__)

type CriticRule = Literal[
    StyleRule.UNSUPPORTED_CLAIM,
    StyleRule.AMBIGUOUS_REFERENCE,
    StyleRule.FILLER,
    StyleRule.OPINION,
    StyleRule.CLICHE,
    StyleRule.TRIPLET,
    StyleRule.INVENTED_EXPERIENCE,
]
type Excerpt = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=STYLE_CRITIC_EXCERPT_MAX_CHARS
    ),
]
type CriticExplanation = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=STYLE_CRITIC_EXPLANATION_MAX_CHARS
    ),
]


class CriticFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    excerpt: Excerpt
    rule: CriticRule
    explanation: CriticExplanation
    violation: bool


class CriticReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    findings: list[CriticFinding]


class CriticOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    violations: list[Violation]
    dropped: int = Field(default=0, ge=0)
    withdrawn: int = Field(default=0, ge=0)
    over_limit: int = Field(default=0, ge=0)


def locate_excerpt(excerpt: str, texts: Sequence[str]) -> int | None:
    for part, text in enumerate(texts, start=1):
        if check_quote(excerpt, text, STYLE_CRITIC_EXCERPT_MIN_CHARS) is QuoteCheck.MATCH:
            return part
    return None


def finding_key(rule: StyleRule, part: int, excerpt: str) -> tuple[StyleRule, int, str]:
    return rule, part, " ".join(text_segments(excerpt))


async def critique_draft(
    client: LLMClient, texts: Sequence[str], fact_set: FactSet, max_findings: int
) -> CriticOutcome:
    reply = await client.complete_json(
        render_critique(texts, fact_set, max_findings),
        CriticReply,
        max_tokens=STYLE_CRITIC_MAX_TOKENS,
    )
    reported = [finding for finding in reply.findings if finding.violation]
    withdrawn = len(reply.findings) - len(reported)
    kept = reported[:max_findings]
    violations: list[Violation] = []
    seen: set[tuple[StyleRule, int, str]] = set()
    dropped = 0
    for finding in kept:
        part = locate_excerpt(finding.excerpt, texts)
        if part is None:
            dropped += 1
            logger.debug("critic finding dropped, excerpt not in the draft: rule=%s", finding.rule)
            continue
        key = finding_key(finding.rule, part, finding.excerpt)
        if key in seen:
            continue
        seen.add(key)
        violations.append(
            Violation(
                rule=finding.rule,
                source=ViolationSource.CRITIC,
                part=part,
                excerpt=finding.excerpt,
                explanation=finding.explanation,
            )
        )
    return CriticOutcome(
        violations=violations,
        dropped=dropped,
        withdrawn=withdrawn,
        over_limit=len(reported) - len(kept),
    )
