from collections import Counter
from collections.abc import Sequence
from html import escape
from typing import NamedTuple

from aiogram.types import InlineKeyboardMarkup

from app.bot import messages
from app.bot.keyboards import draft_keyboard
from app.config.constants import (
    LINE_SEPARATOR,
    PARAGRAPH_SEPARATOR,
    TELEGRAM_MESSAGE_MAX_CHARS,
    WEAK_USED_WARNING_RATIO,
    WORD_SEPARATOR,
)
from app.domain.draft import Draft, LengthIssue, LengthViolation, PostFormat
from app.domain.fact import Fact, FactSet
from app.domain.pipeline import (
    DraftExpired,
    NoSources,
    NotEnoughFacts,
    NothingFound,
    PostReady,
    ResearchFailed,
    ReworkOutcome,
    StepFailed,
    ThreadUnavailable,
    TopicOutcome,
)
from app.domain.research import SourceFailure
from app.domain.snippet import url_host
from app.domain.style import CriticStatus, StyleRule, Violation
from app.prompts.writing import disputed_ids
from app.services.short_post import PARAGRAPH_BREAK, split_sentences

SEPARATELY_SHOWN_RULES = frozenset({StyleRule.LENGTH, StyleRule.UNVERIFIED_NUMBER})


class OutgoingMessage(NamedTuple):
    text: str
    html: bool = False
    keyboard: InlineKeyboardMarkup | None = None


class Rendered(NamedTuple):
    status: str | None
    messages: list[OutgoingMessage]


def hard_split(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(WORD_SEPARATOR, 0, limit + 1)
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def pack(items: Sequence[str], limit: int, separator: str) -> list[str]:
    chunks: list[str] = []
    current = ""
    for item in items:
        candidate = f"{current}{separator}{item}" if current else item
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = item
    if current:
        chunks.append(current)
    return chunks


def split_paragraph(paragraph: str, limit: int) -> list[str]:
    pieces: list[str] = []
    for sentence in split_sentences(paragraph):
        pieces.extend(hard_split(sentence, limit))
    return pack(pieces, limit, WORD_SEPARATOR)


def split_plain(text: str, limit: int = TELEGRAM_MESSAGE_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in (piece.strip() for piece in PARAGRAPH_BREAK.split(text)):
        if not paragraph:
            continue
        if len(paragraph) > limit:
            chunks.extend(pack(current, limit, PARAGRAPH_SEPARATOR))
            current = []
            chunks.extend(split_paragraph(paragraph, limit))
            continue
        current.append(paragraph)
    chunks.extend(pack(current, limit, PARAGRAPH_SEPARATOR))
    return chunks


def pack_blocks(blocks: Sequence[str], limit: int = TELEGRAM_MESSAGE_MAX_CHARS) -> list[str]:
    items: list[str] = []
    for block in blocks:
        if len(block) <= limit:
            items.append(block)
        else:
            items.extend(pack(block.split(LINE_SEPARATOR), limit, LINE_SEPARATOR))
    return pack(items, limit, PARAGRAPH_SEPARATOR)


def post_messages(draft: Draft) -> list[str]:
    return [piece for part in draft.rendered for piece in split_plain(part)]


def failures_list(failures: Sequence[SourceFailure]) -> str:
    counts = Counter((failure.source, failure.kind) for failure in failures)
    return messages.FAILURE_SEPARATOR.join(
        messages.FAILURE_ITEM_TEMPLATE.format(source=source, kind=kind)
        if count == 1
        else messages.FAILURE_ITEM_COUNT_TEMPLATE.format(source=source, kind=kind, count=count)
        for (source, kind), count in counts.items()
    )


def failures_note(failures: Sequence[SourceFailure]) -> str | None:
    if not failures:
        return None
    return messages.FAILURES_NOTE_TEMPLATE.format(failures=failures_list(failures))


def dispute_reasons(fact: Fact, fact_set: FactSet) -> list[str]:
    reasons = [
        messages.DISPUTE_REASON_TEMPLATE.format(
            others=messages.ID_SEPARATOR.join(
                fact_id for fact_id in dispute.fact_ids if fact_id != fact.id
            ),
            explanation=escape(dispute.explanation),
        )
        for dispute in fact_set.disputes
        if fact.id in dispute.fact_ids
    ]
    return reasons or [messages.UNGROUPED_DISPUTE_REASON_LINE]


def fact_links(fact: Fact) -> list[str]:
    weak_by_url: dict[str, bool] = {}
    for ref in fact.support:
        weak_by_url[ref.url] = weak_by_url.get(ref.url, True) and ref.weak
    return [
        (messages.WEAK_LINK_TEMPLATE if weak else messages.LINK_TEMPLATE).format(
            url=escape(url, quote=True), label=escape(url_host(url) or url)
        )
        for url, weak in weak_by_url.items()
    ]


def fact_block(fact: Fact, fact_set: FactSet, disputed: set[str]) -> str:
    is_disputed = fact.id in disputed
    status = messages.DISPUTED_STATUS_LABEL if is_disputed else messages.STATUS_LABELS[fact.status]
    lines = [
        messages.FACT_HEADER_TEMPLATE.format(fact_id=escape(fact.id), status=status),
        escape(fact.text),
    ]
    if is_disputed:
        lines.extend(dispute_reasons(fact, fact_set))
    lines.extend(fact_links(fact))
    return LINE_SEPARATOR.join(lines)


def fact_blocks(facts: Sequence[Fact], fact_set: FactSet) -> list[str]:
    disputed = disputed_ids(fact_set)
    return [fact_block(fact, fact_set, disputed) for fact in facts]


def facts_messages(ready: PostReady) -> list[str]:
    fact_set = ready.fact_set
    used_ids = set(ready.result.draft.used_fact_ids)
    used = [fact for fact in fact_set.facts if fact.id in used_ids]
    unused = [fact for fact in fact_set.facts if fact.id not in used_ids]
    if ready.variant:
        blocks = [
            messages.VARIANT_FACTS_HEADER_TEMPLATE.format(used=len(used)),
            *fact_blocks(used, fact_set),
        ]
        if unused:
            blocks.append(messages.OTHER_FACTS_TEMPLATE.format(count=len(unused)))
        return pack_blocks(blocks)
    blocks = [messages.FACTS_HEADER_TEMPLATE.format(used=len(used), total=len(fact_set.facts))]
    if used:
        blocks.extend([messages.USED_SECTION, *fact_blocks(used, fact_set)])
    if unused:
        blocks.extend([messages.UNUSED_SECTION, *fact_blocks(unused, fact_set)])
    note = failures_note(ready.failures)
    if note is not None:
        blocks.append(escape(note))
    return pack_blocks(blocks)


def insufficient_messages(outcome: NotEnoughFacts) -> list[str]:
    fact_set = outcome.extraction.fact_set
    blocks: list[str] = []
    if fact_set.facts:
        blocks.extend(
            [
                messages.INSUFFICIENT_FACTS_HEADER_TEMPLATE.format(total=len(fact_set.facts)),
                *fact_blocks(fact_set.facts, fact_set),
            ]
        )
    note = failures_note(outcome.failures)
    if note is not None:
        blocks.append(escape(note))
    return pack_blocks(blocks) if blocks else []


def part_name(post_format: PostFormat, part: int | None) -> str:
    if post_format is PostFormat.THREAD and part is not None:
        return messages.TWEET_PART_TEMPLATE.format(part=part)
    return messages.POST_PART_NAME


def length_warning(violation: LengthViolation, post_format: PostFormat) -> str:
    match violation.issue:
        case LengthIssue.TOO_MANY_PARTS:
            detail = messages.LENGTH_TOO_MANY_PARTS_TEMPLATE.format(
                actual=violation.actual, limit=violation.limit
            )
        case LengthIssue.TOO_FEW_FACTS:
            detail = messages.LENGTH_TOO_FEW_FACTS_TEMPLATE.format(
                actual=violation.actual, limit=violation.limit
            )
        case LengthIssue.TOO_SHORT:
            detail = messages.LENGTH_TOO_SHORT_TEMPLATE.format(
                part=part_name(post_format, violation.part),
                actual=violation.actual,
                limit=violation.limit,
            )
        case LengthIssue.PART_TOO_LONG:
            detail = messages.LENGTH_PART_TEMPLATE.format(
                part=part_name(post_format, violation.part),
                actual=violation.actual,
                limit=violation.limit,
            )
    return f"{messages.LENGTH_PREFIX}{detail}"


def violation_warning(violation: Violation, post_format: PostFormat) -> str:
    excerpt = (
        messages.VIOLATION_EXCERPT_TEMPLATE.format(excerpt=violation.excerpt)
        if violation.excerpt
        else ""
    )
    where = (
        messages.VIOLATION_PART_TEMPLATE.format(part=part_name(post_format, violation.part))
        if post_format is PostFormat.THREAD and violation.part is not None
        else ""
    )
    return messages.VIOLATION_TEMPLATE.format(
        rule=messages.RULE_LABELS[violation.rule],
        where=f"{excerpt}{where}",
        explanation=violation.explanation,
    )


def weak_facts_warning(ready: PostReady) -> str | None:
    used_ids = set(ready.result.draft.used_fact_ids)
    used = [fact for fact in ready.fact_set.facts if fact.id in used_ids]
    weak = sum(fact.weak_only for fact in used)
    if not used or weak / len(used) <= WEAK_USED_WARNING_RATIO:
        return None
    return messages.WEAK_FACTS_TEMPLATE.format(weak=weak, total=len(used))


def warnings(ready: PostReady) -> list[str]:
    result = ready.result
    draft = result.draft
    found: list[str] = []
    if ready.downgrade is not None:
        found.append(
            messages.THREAD_DOWNGRADE_TEMPLATE.format(
                required=ready.downgrade.required, assertable=ready.downgrade.assertable
            )
        )
    weak_note = weak_facts_warning(ready)
    if weak_note is not None:
        found.append(weak_note)
    if draft.unverified_numbers:
        found.append(
            messages.UNVERIFIED_NUMBERS_TEMPLATE.format(
                numbers=messages.NUMBER_SEPARATOR.join(draft.unverified_numbers)
            )
        )
    found.extend(
        length_warning(violation, draft.post_format) for violation in draft.length_violations
    )
    found.extend(
        violation_warning(violation, draft.post_format)
        for violation in result.report.violations
        if violation.rule not in SEPARATELY_SHOWN_RULES
    )
    if result.report.critic is CriticStatus.FAILED:
        found.append(messages.CRITIC_FAILED_TEXT)
    if result.regeneration_failed:
        found.append(messages.REGENERATION_FAILED_TEXT)
    if result.regressions_rejected:
        found.append(
            messages.REGRESSIONS_REJECTED_TEMPLATE.format(count=result.regressions_rejected)
        )
    if draft.dropped_tail:
        found.append(
            messages.DROPPED_TAIL_TEMPLATE.format(
                pieces=messages.DROPPED_PIECE_SEPARATOR.join(
                    messages.DROPPED_PIECE_TEMPLATE.format(piece=piece)
                    for piece in draft.dropped_tail
                )
            )
        )
    return found


def warnings_messages(ready: PostReady) -> list[str]:
    found = warnings(ready)
    if not found:
        return []
    text = LINE_SEPARATOR.join(
        [
            messages.WARNINGS_HEADER,
            *(messages.WARNING_LINE_TEMPLATE.format(text=line) for line in found),
        ]
    )
    return split_plain(text)


def post_ready_messages(ready: PostReady) -> list[OutgoingMessage]:
    posts = post_messages(ready.result.draft)
    keyboard = draft_keyboard(ready.draft_id, ready.actions)
    outgoing = [OutgoingMessage(text=text) for text in posts[:-1]]
    outgoing.append(OutgoingMessage(text=posts[-1], keyboard=keyboard))
    outgoing.extend(OutgoingMessage(text=text) for text in warnings_messages(ready))
    outgoing.extend(OutgoingMessage(text=text, html=True) for text in facts_messages(ready))
    return outgoing


def step_failed_text(outcome: StepFailed) -> str:
    return messages.STEP_FAILED_TEMPLATE.format(
        stage=messages.STAGE_NAMES[outcome.stage], reason=messages.FAILURE_REASONS[outcome.kind]
    )


def render_topic_outcome(outcome: TopicOutcome) -> Rendered:
    match outcome:
        case PostReady():
            return Rendered(status=None, messages=post_ready_messages(outcome))
        case NoSources():
            return Rendered(status=messages.NO_SOURCES_TEXT, messages=[])
        case ResearchFailed():
            return Rendered(
                status=messages.RESEARCH_FAILED_TEMPLATE.format(
                    failures=failures_list(outcome.failures)
                ),
                messages=[],
            )
        case NothingFound():
            note = failures_note(outcome.failures)
            status = (
                messages.NOTHING_FOUND_TEXT
                if note is None
                else f"{messages.NOTHING_FOUND_TEXT}{PARAGRAPH_SEPARATOR}{note}"
            )
            return Rendered(status=status, messages=[])
        case NotEnoughFacts():
            extraction = outcome.extraction
            status = messages.NOT_ENOUGH_FACTS_TEMPLATE.format(
                assertable=extraction.assertable_count,
                disputed=outcome.disputed,
                required=extraction.required,
            )
            return Rendered(
                status=status,
                messages=[
                    OutgoingMessage(text=text, html=True) for text in insufficient_messages(outcome)
                ],
            )
        case StepFailed():
            return Rendered(status=step_failed_text(outcome), messages=[])


def render_rework_outcome(outcome: ReworkOutcome) -> Rendered:
    match outcome:
        case PostReady():
            return Rendered(status=None, messages=post_ready_messages(outcome))
        case DraftExpired():
            return Rendered(status=messages.STALE_BUTTONS_TEXT, messages=[])
        case ThreadUnavailable():
            return Rendered(
                status=messages.THREAD_UNAVAILABLE_TEMPLATE.format(
                    required=outcome.required, assertable=outcome.assertable
                ),
                messages=[],
            )
        case StepFailed():
            return Rendered(status=step_failed_text(outcome), messages=[])
