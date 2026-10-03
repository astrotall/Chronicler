import logging
from collections.abc import Sequence
from typing import Annotated, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.config.constants import (
    SHORT_DEFAULT_LENGTH_RETRIES,
    SHORT_DEFAULT_MAX_FACTS,
    SHORT_DEFAULT_SENTENCE_CHARS,
    THREAD_MIN_TWEETS,
    THREAD_NUMBERING_TEMPLATE,
    WRITING_LENGTH_RETRIES,
    WRITING_LONG_MAX_TOKENS,
    WRITING_SHORT_MAX_TOKENS,
    WRITING_THREAD_MAX_TOKENS,
)
from app.config.settings import Settings
from app.domain.draft import (
    Draft,
    DraftPart,
    LengthIssue,
    LengthViolation,
    LongSize,
    PostFormat,
    Revision,
    SentenceBudget,
)
from app.domain.fact import FactSet
from app.domain.llm import Message, Role
from app.llm.client import LLMClient
from app.prompts.writing import (
    NO_LONG_SIZE,
    disputed_ids,
    render_expand_correction,
    render_length_correction,
    render_short_correction,
    render_writing,
)
from app.services.disputes import with_disputed_facts
from app.services.facts import normalize_label
from app.services.quote_check import extract_numbers
from app.services.short_post import (
    drop_tail,
    mentions_disputed_numbers,
    select_short_facts,
    sentence_budget,
    sentences_to_cut,
)

logger = logging.getLogger(__name__)

type ReplyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

MAX_TOKENS: dict[PostFormat, int] = {
    PostFormat.SHORT: WRITING_SHORT_MAX_TOKENS,
    PostFormat.LONG: WRITING_LONG_MAX_TOKENS,
    PostFormat.THREAD: WRITING_THREAD_MAX_TOKENS,
}


SIZE_ISSUES = frozenset({LengthIssue.TOO_SHORT, LengthIssue.TOO_FEW_FACTS})


class SingleReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_ids: list[str]
    text: ReplyText


class ThreadReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_ids: list[str]
    tweets: list[ReplyText] = Field(min_length=THREAD_MIN_TWEETS)


type WritingReply = SingleReply | ThreadReply


def numbering_prefix(index: int) -> str:
    return THREAD_NUMBERING_TEMPLATE.format(index=index)


class WritingLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    short_max_chars: int = Field(ge=1)
    long_max_chars: int = Field(ge=1)
    thread_tweet_max_chars: int = Field(ge=1)
    thread_max_tweets: int = Field(ge=THREAD_MIN_TWEETS)
    thread_numbering: bool = False
    short_max_facts: int = Field(default=SHORT_DEFAULT_MAX_FACTS, ge=1)
    short_sentence_chars: int = Field(default=SHORT_DEFAULT_SENTENCE_CHARS, ge=1)
    short_length_retries: int = Field(default=SHORT_DEFAULT_LENGTH_RETRIES, ge=0)
    short_drop_tail: bool = False
    long_min_chars: int = Field(default=0, ge=0)
    long_min_used_facts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_long_range(self) -> Self:
        if self.long_min_chars > self.long_max_chars:
            raise ValueError("the long minimum must not exceed the long limit")
        return self

    @model_validator(mode="after")
    def require_room_for_numbering(self) -> Self:
        if self.text_max_chars(PostFormat.THREAD) < 1:
            raise ValueError("the tweet limit must leave room for the numbering")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            short_max_chars=settings.short_max_chars,
            long_max_chars=settings.long_max_chars,
            thread_tweet_max_chars=settings.thread_tweet_max_chars,
            thread_max_tweets=settings.thread_max_tweets,
            thread_numbering=settings.thread_numbering,
            short_max_facts=settings.short_max_facts,
            short_sentence_chars=settings.short_sentence_chars,
            short_length_retries=settings.short_length_retries,
            short_drop_tail=settings.short_drop_tail,
            long_min_chars=settings.long_min_chars,
            long_min_used_facts=settings.long_min_used_facts,
        )

    def part_max_chars(self, post_format: PostFormat) -> int:
        match post_format:
            case PostFormat.SHORT:
                return self.short_max_chars
            case PostFormat.LONG:
                return self.long_max_chars
            case PostFormat.THREAD:
                return self.thread_tweet_max_chars

    def numbered(self, post_format: PostFormat) -> bool:
        return post_format is PostFormat.THREAD and self.thread_numbering

    def text_max_chars(self, post_format: PostFormat) -> int:
        reserved = (
            len(numbering_prefix(self.thread_max_tweets)) if self.numbered(post_format) else 0
        )
        return self.part_max_chars(post_format) - reserved

    def length_retries(self, post_format: PostFormat) -> int:
        if post_format is PostFormat.SHORT:
            return self.short_length_retries
        return WRITING_LENGTH_RETRIES

    @property
    def short_budget(self) -> SentenceBudget:
        return sentence_budget(self.short_max_chars, self.short_sentence_chars)


def reply_texts(reply: WritingReply) -> list[str]:
    match reply:
        case ThreadReply():
            return list(reply.tweets)
        case SingleReply():
            return [reply.text]


def build_parts(
    texts: Sequence[str], post_format: PostFormat, limits: WritingLimits
) -> list[DraftPart]:
    if not limits.numbered(post_format):
        return [DraftPart(text=text) for text in texts]
    return [
        DraftPart(text=text, prefix=numbering_prefix(index))
        for index, text in enumerate(texts, start=1)
    ]


def check_length(
    parts: Sequence[DraftPart], post_format: PostFormat, limits: WritingLimits
) -> list[LengthViolation]:
    limit = limits.part_max_chars(post_format)
    violations = [
        LengthViolation(
            issue=LengthIssue.PART_TOO_LONG, part=index, actual=len(part.rendered), limit=limit
        )
        for index, part in enumerate(parts, start=1)
        if len(part.rendered) > limit
    ]
    if post_format is PostFormat.THREAD and len(parts) > limits.thread_max_tweets:
        violations.append(
            LengthViolation(
                issue=LengthIssue.TOO_MANY_PARTS,
                actual=len(parts),
                limit=limits.thread_max_tweets,
            )
        )
    return violations


def long_size(post_format: PostFormat, limits: WritingLimits, prompt_facts: FactSet) -> LongSize:
    if post_format is not PostFormat.LONG:
        return NO_LONG_SIZE
    disputed = disputed_ids(prompt_facts)
    assertable = sum(1 for fact in prompt_facts.facts if fact.id not in disputed)
    return LongSize(
        min_chars=limits.long_min_chars,
        min_facts=min(limits.long_min_used_facts, assertable),
    )


def check_size(
    parts: Sequence[DraftPart], used_fact_ids: Sequence[str], size: LongSize
) -> list[LengthViolation]:
    violations: list[LengthViolation] = []
    chars = sum(len(part.text) for part in parts)
    if chars < size.min_chars:
        violations.append(
            LengthViolation(issue=LengthIssue.TOO_SHORT, part=1, actual=chars, limit=size.min_chars)
        )
    if len(used_fact_ids) < size.min_facts:
        violations.append(
            LengthViolation(
                issue=LengthIssue.TOO_FEW_FACTS,
                actual=len(used_fact_ids),
                limit=size.min_facts,
            )
        )
    return violations


def unused_fact_ids(prompt_facts: FactSet, used_fact_ids: Sequence[str]) -> list[str]:
    disputed = disputed_ids(prompt_facts)
    return [
        fact.id
        for fact in prompt_facts.facts
        if fact.id not in disputed and fact.id not in used_fact_ids
    ]


def text_violations(
    parts: Sequence[DraftPart], violations: Sequence[LengthViolation]
) -> list[LengthViolation]:
    adjusted: list[LengthViolation] = []
    for violation in violations:
        if violation.part is None:
            adjusted.append(violation)
            continue
        prefix = len(parts[violation.part - 1].prefix)
        adjusted.append(
            violation.model_copy(
                update={"actual": violation.actual - prefix, "limit": violation.limit - prefix}
            )
        )
    return adjusted


def collect_fact_ids(reported: Sequence[str], fact_set: FactSet) -> tuple[list[str], int]:
    known = {fact.id for fact in fact_set.facts}
    used: list[str] = []
    unknown = 0
    for label in reported:
        fact_id = normalize_label(label)
        if fact_id not in known:
            unknown += 1
        elif fact_id not in used:
            used.append(fact_id)
    return used, unknown


def number_order(number: str) -> tuple[float, str]:
    return float(number), number


def unverified_numbers(texts: Sequence[str], fact_set: FactSet) -> list[str]:
    available: set[str] = set()
    for fact in fact_set.facts:
        available |= extract_numbers(fact.text)
    found: set[str] = set()
    for text in texts:
        found |= extract_numbers(text)
    return sorted(found - available, key=number_order)


def facts_for_prompt(fact_set: FactSet, post_format: PostFormat, limits: WritingLimits) -> FactSet:
    if post_format is PostFormat.SHORT:
        return select_short_facts(fact_set, limits.short_max_facts)
    return fact_set


def length_correction(
    parts: Sequence[DraftPart],
    violations: Sequence[LengthViolation],
    post_format: PostFormat,
    limits: WritingLimits,
    unused: Sequence[str],
) -> Message:
    if any(violation.issue in SIZE_ISSUES for violation in violations):
        return render_expand_correction(violations, unused)
    if post_format is not PostFormat.SHORT:
        return render_length_correction(text_violations(parts, violations))
    [violation] = violations
    text = parts[0].text
    return render_short_correction(
        violation.actual,
        violation.limit,
        sentences_to_cut(text, violation.actual - violation.limit),
        limits.short_budget,
    )


def tail_to_drop(
    parts: Sequence[DraftPart], fact_set: FactSet, limits: WritingLimits
) -> tuple[str, list[str]] | None:
    text = parts[0].text
    if mentions_disputed_numbers(text, fact_set):
        return None
    return drop_tail(text, limits.short_max_chars)


async def request_reply(
    client: LLMClient, messages: Sequence[Message], post_format: PostFormat
) -> WritingReply:
    max_tokens = MAX_TOKENS[post_format]
    if post_format is PostFormat.THREAD:
        return await client.complete_json(messages, ThreadReply, max_tokens=max_tokens)
    return await client.complete_json(messages, SingleReply, max_tokens=max_tokens)


class Assessment(NamedTuple):
    parts: list[DraftPart]
    reported: list[str]
    used: list[str]
    unknown: int
    violations: list[LengthViolation]


def assess(
    reply: WritingReply,
    post_format: PostFormat,
    limits: WritingLimits,
    fact_set: FactSet,
    prompt_facts: FactSet,
    size: LongSize,
) -> Assessment:
    parts = build_parts(reply_texts(reply), post_format, limits)
    reported, unknown = collect_fact_ids(reply.fact_ids, fact_set)
    used = with_disputed_facts(reported, [part.text for part in parts], prompt_facts)
    violations = [*check_length(parts, post_format, limits), *check_size(parts, used, size)]
    return Assessment(parts, reported, used, unknown, violations)


async def write_draft(
    client: LLMClient,
    fact_set: FactSet,
    post_format: PostFormat,
    limits: WritingLimits,
    examples: Sequence[str] = (),
    *,
    angle: str | None = None,
    revision: Revision | None = None,
) -> Draft:
    if not fact_set.facts:
        raise ValueError("fact set must not be empty")
    framing = angle.strip() if angle is not None and angle.strip() else None
    prompt_facts = facts_for_prompt(fact_set, post_format, limits)
    size = long_size(post_format, limits, prompt_facts)
    messages = render_writing(
        prompt_facts,
        post_format,
        max_chars=limits.text_max_chars(post_format),
        max_tweets=limits.thread_max_tweets,
        budget=limits.short_budget,
        long_size=size,
        examples=examples,
        angle=framing,
        revision=revision,
    )
    reply = await request_reply(client, messages, post_format)
    attempts = 1
    assessment = assess(reply, post_format, limits, fact_set, prompt_facts, size)
    for _ in range(limits.length_retries(post_format)):
        if not assessment.violations:
            break
        messages = [
            *messages,
            Message(role=Role.ASSISTANT, content=reply.model_dump_json()),
            length_correction(
                assessment.parts,
                assessment.violations,
                post_format,
                limits,
                unused_fact_ids(prompt_facts, assessment.used),
            ),
        ]
        reply = await request_reply(client, messages, post_format)
        attempts += 1
        assessment = assess(reply, post_format, limits, fact_set, prompt_facts, size)
    parts = assessment.parts
    violations = assessment.violations
    used = assessment.used
    dropped: list[str] = []
    if violations and post_format is PostFormat.SHORT and limits.short_drop_tail:
        trimmed = tail_to_drop(parts, fact_set, limits)
        if trimmed is not None:
            kept, dropped = trimmed
            parts = [DraftPart(text=kept)]
            violations = check_length(parts, post_format, limits)
            used = with_disputed_facts(assessment.reported, [kept], prompt_facts)
    draft = Draft(
        post_format=post_format,
        parts=parts,
        used_fact_ids=used,
        unverified_numbers=unverified_numbers([part.text for part in parts], fact_set),
        length_violations=violations,
        attempts=attempts,
        dropped_tail=dropped,
    )
    logger.info(
        "draft written format=%s parts=%d offered_facts=%d used_facts=%d unknown_fact_ids=%d "
        "unverified_numbers=%d length_violations=%d attempts=%d dropped_tail=%d",
        post_format,
        len(draft.parts),
        len(prompt_facts.facts),
        len(used),
        assessment.unknown,
        len(draft.unverified_numbers),
        len(violations),
        attempts,
        len(dropped),
    )
    return draft
