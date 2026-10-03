from collections.abc import Sequence

from app.config.constants import (
    LONG_MIN_PARAGRAPHS,
    LONG_PARAGRAPH_CHARS,
    LONG_TYPICAL_SIZE_MULTIPLIER,
    THREAD_MIN_TWEETS,
)
from app.domain.draft import (
    LengthIssue,
    LengthViolation,
    LongSize,
    PostFormat,
    Revision,
    SentenceBudget,
)
from app.domain.fact import Fact, FactSet, FactStatus
from app.domain.llm import Message, Role
from app.prompts.style_rules import render_style_rules

WRITING_SYSTEM_PROMPT = (
    "You write a post for the author's history account on X. The post is in Russian and "
    "sounds like the author.\n"
    "Rules:\n"
    "1. Every claim, name, number and date in the post comes from the facts the user sends. "
    "Do not add anything from your own knowledge, even if you are sure it is true. You may "
    "leave facts out. Do not combine facts into a new claim that none of them states. A "
    "cause, a consequence or a claim of importance that no fact states is such a new claim.\n"
    "2. The topic says what the post is about and what could hook the reader. It is a frame, "
    "not a source: never take a claim, a name, a number or a date from it.\n"
    "3. Write numbers and dates with digits, exactly as the facts write them. Do not round, "
    "convert or add up numbers.\n"
    "4. The facts are notes, not text to paste. Retell them in your own words.\n"
    "5. Disputed facts are listed in their own block. Never state them as established. "
    "Mention one only together with the other version and say that the sources disagree, "
    "or leave it out.\n"
    "6. {format_rule}\n"
    "7. The json reply is only an envelope for the post. First list in fact_ids the ids of "
    "the facts the post uses, exactly as given (for example F3), and also the disputed facts "
    "the post mentions, in whichever wording. Then write the post itself "
    "as you would write it for publication. Separate paragraphs with a blank line.\n"
    "\n"
    "{style_rules}"
)
SHORT_FORMAT_RULE = (
    "Format: one short post in the field text: at most {max_sentences} sentences, each "
    "sentence at most {sentence_chars} characters, and at most {max_chars} characters in "
    "total including spaces. A sentence carries one fact, at most two. If the facts do not "
    "fit, leave some of them out."
)
LONG_FORMAT_RULE = (
    "Format: one long post in the field text, at most {max_chars} characters including "
    "spaces. Make it as long as the facts deserve and do not pad it."
)
LONG_SIZED_FORMAT_RULE = (
    "Format: one long post in the field text, several paragraphs separated by a blank line."
    "{size} The limit of {max_chars} characters including spaces is a ceiling, not a target. "
    "Do not pad the text, and do not shrink it to a summary of a few lines."
)
LONG_MIN_CHARS_CLAUSE = (
    " Write at least {min_paragraphs} paragraphs, usually {min_chars} to {typical_chars} "
    "characters, never fewer than {min_chars}."
)
LONG_MIN_FACTS_CLAUSE = (
    " Develop at least {min_facts} of the facts you may state, each in its own sentences, "
    "not as a list."
)
NO_LONG_SIZE = LongSize(min_chars=0, min_facts=0)
THREAD_FORMAT_RULE = (
    "Format: a thread of {min_tweets} to {max_tweets} tweets in the field tweets. Split at "
    "meaning boundaries, never in the middle of a sentence. A tweet may be one short "
    "sentence and needs no closing line of its own, but it is never a fragment: the reader "
    "can tell who and what it is about. Each tweet has at most {max_chars} characters "
    "including spaces. Do not number the tweets."
)
EXAMPLES_PROMPT = (
    "Reference posts by the author. They show the rhythm and the manner only. Do not copy "
    "their wording, their topics or their facts: a fact from an example is not a fact you "
    "may use.\n\n{examples}"
)
EXAMPLE_SEPARATOR = "\n\n---\n\n"

TOPIC_BLOCK = "Topic (a frame, not a source): {topic}"
FACTS_BLOCK = "Facts you may state:\n{facts}"
DISPUTES_BLOCK = (
    "Disputed facts. The sources disagree. Never state any of these as established:\n{disputes}"
)
DISPUTE_TEMPLATE = "Disagreement: {explanation}\n{facts}"
UNGROUPED_DISPUTE_EXPLANATION = "the sources disagree on this"
ANGLE_BLOCK = "Angle requested by the author: {angle}"
PREVIOUS_BLOCK = "Previous version of the post:\n{previous}"
REVISION_BLOCK = "Revision requested by the author: {instruction}"
FACT_LINE_TEMPLATE = "{fact_id} [{status}]: {text}"
DISPUTED_FACT_LINE_TEMPLATE = "{fact_id}: {text}"
PREVIOUS_PART_TEMPLATE = "[{index}]\n{text}"
LINE_SEPARATOR = "\n"
BLOCK_SEPARATOR = "\n\n"

LENGTH_CORRECTION_TEMPLATE = (
    "The post breaks the length limits:\n{problems}\n"
    "Rewrite it so that it fits. Shorten the text, keep whole sentences, never cut a "
    "sentence in the middle, and do not add or change facts. Reply with the same json shape."
)
PART_TOO_LONG_LINE = "- part {part}: {actual} characters, the limit is {limit}"
TOO_MANY_PARTS_LINE = "- {actual} tweets, the maximum is {limit}"
EXPAND_CORRECTION_TEMPLATE = (
    "The post is too short:\n{problems}\n"
    "Rewrite it as a developed post: expand the text and use more of the facts you may "
    "state.{unused} Retell each fact in your own words. Do not pad the text, and do not add "
    "anything that no fact states. Reply with the same json shape."
)
TOO_SHORT_LINE = "- {actual} characters, the minimum is {limit}"
TOO_FEW_FACTS_LINE = "- {actual} facts used, the minimum is {limit}"
UNUSED_FACTS_CLAUSE = " Facts not used yet: {ids}."
ID_LIST_SEPARATOR = ", "
SHORT_CORRECTION_TEMPLATE = (
    "The post has {actual} characters, {excess} over the limit of {limit}. Delete these "
    "sentences, or shorten them by at least {excess} characters in total:\n{sentences}\n"
    "Keep at most {max_sentences} sentences. Keep whole sentences, never cut a sentence in "
    "the middle, and do not add or change facts. Reply with the same json shape."
)
NAMED_SENTENCE_LINE = "- «{sentence}»"


def long_rule(max_chars: int, long_size: LongSize) -> str:
    if not long_size.min_chars and not long_size.min_facts:
        return LONG_FORMAT_RULE.format(max_chars=max_chars)
    size = ""
    if long_size.min_chars:
        size += LONG_MIN_CHARS_CLAUSE.format(
            min_paragraphs=max(LONG_MIN_PARAGRAPHS, long_size.min_chars // LONG_PARAGRAPH_CHARS),
            min_chars=long_size.min_chars,
            typical_chars=long_size.min_chars * LONG_TYPICAL_SIZE_MULTIPLIER,
        )
    if long_size.min_facts:
        size += LONG_MIN_FACTS_CLAUSE.format(min_facts=long_size.min_facts)
    return LONG_SIZED_FORMAT_RULE.format(size=size, max_chars=max_chars)


def format_rule(
    post_format: PostFormat,
    max_chars: int,
    max_tweets: int,
    budget: SentenceBudget,
    long_size: LongSize,
) -> str:
    match post_format:
        case PostFormat.SHORT:
            return SHORT_FORMAT_RULE.format(
                max_sentences=budget.max_sentences,
                sentence_chars=budget.sentence_chars,
                max_chars=max_chars,
            )
        case PostFormat.LONG:
            return long_rule(max_chars, long_size)
        case PostFormat.THREAD:
            return THREAD_FORMAT_RULE.format(
                min_tweets=THREAD_MIN_TWEETS, max_tweets=max_tweets, max_chars=max_chars
            )


def disputed_ids(fact_set: FactSet) -> set[str]:
    grouped = {fact_id for dispute in fact_set.disputes for fact_id in dispute.fact_ids}
    flagged = {fact.id for fact in fact_set.facts if fact.status is FactStatus.DISPUTED}
    return grouped | flagged


def fact_lines(facts: Sequence[Fact]) -> str:
    return LINE_SEPARATOR.join(
        FACT_LINE_TEMPLATE.format(fact_id=fact.id, status=fact.status.value, text=fact.text)
        for fact in facts
    )


def disputed_lines(facts: Sequence[Fact]) -> str:
    return LINE_SEPARATOR.join(
        DISPUTED_FACT_LINE_TEMPLATE.format(fact_id=fact.id, text=fact.text) for fact in facts
    )


def dispute_blocks(fact_set: FactSet, disputed: set[str]) -> list[str]:
    by_id = {fact.id: fact for fact in fact_set.facts}
    blocks: list[str] = []
    covered: set[str] = set()
    for dispute in fact_set.disputes:
        members = [by_id[fact_id] for fact_id in dispute.fact_ids if fact_id in by_id]
        if not members:
            continue
        covered.update(fact.id for fact in members)
        blocks.append(
            DISPUTE_TEMPLATE.format(explanation=dispute.explanation, facts=disputed_lines(members))
        )
    ungrouped = [fact for fact in fact_set.facts if fact.id in disputed and fact.id not in covered]
    if ungrouped:
        blocks.append(
            DISPUTE_TEMPLATE.format(
                explanation=UNGROUPED_DISPUTE_EXPLANATION, facts=disputed_lines(ungrouped)
            )
        )
    return blocks


def previous_text(parts: Sequence[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return BLOCK_SEPARATOR.join(
        PREVIOUS_PART_TEMPLATE.format(index=index, text=text)
        for index, text in enumerate(parts, start=1)
    )


def render_writing(
    fact_set: FactSet,
    post_format: PostFormat,
    *,
    max_chars: int,
    max_tweets: int,
    budget: SentenceBudget,
    long_size: LongSize = NO_LONG_SIZE,
    examples: Sequence[str] = (),
    angle: str | None = None,
    revision: Revision | None = None,
) -> list[Message]:
    disputed = disputed_ids(fact_set)
    assertable = [fact for fact in fact_set.facts if fact.id not in disputed]
    blocks = [TOPIC_BLOCK.format(topic=fact_set.topic)]
    if assertable:
        blocks.append(FACTS_BLOCK.format(facts=fact_lines(assertable)))
    disputes = dispute_blocks(fact_set, disputed)
    if disputes:
        blocks.append(DISPUTES_BLOCK.format(disputes=BLOCK_SEPARATOR.join(disputes)))
    if angle is not None:
        blocks.append(ANGLE_BLOCK.format(angle=angle))
    if revision is not None:
        blocks.append(PREVIOUS_BLOCK.format(previous=previous_text(revision.previous)))
        blocks.append(REVISION_BLOCK.format(instruction=revision.instruction))
    messages = [
        Message(
            role=Role.SYSTEM,
            content=WRITING_SYSTEM_PROMPT.format(
                format_rule=format_rule(post_format, max_chars, max_tweets, budget, long_size),
                style_rules=render_style_rules(),
            ),
        )
    ]
    if examples:
        messages.append(
            Message(
                role=Role.SYSTEM,
                content=EXAMPLES_PROMPT.format(examples=EXAMPLE_SEPARATOR.join(examples)),
            )
        )
    messages.append(Message(role=Role.USER, content=BLOCK_SEPARATOR.join(blocks)))
    return messages


def violation_line(violation: LengthViolation) -> str:
    match violation.issue:
        case LengthIssue.PART_TOO_LONG:
            return PART_TOO_LONG_LINE.format(
                part=violation.part, actual=violation.actual, limit=violation.limit
            )
        case LengthIssue.TOO_MANY_PARTS:
            return TOO_MANY_PARTS_LINE.format(actual=violation.actual, limit=violation.limit)
        case LengthIssue.TOO_SHORT:
            return TOO_SHORT_LINE.format(actual=violation.actual, limit=violation.limit)
        case LengthIssue.TOO_FEW_FACTS:
            return TOO_FEW_FACTS_LINE.format(actual=violation.actual, limit=violation.limit)


def render_length_correction(violations: Sequence[LengthViolation]) -> Message:
    problems = LINE_SEPARATOR.join(violation_line(violation) for violation in violations)
    return Message(role=Role.USER, content=LENGTH_CORRECTION_TEMPLATE.format(problems=problems))


def render_expand_correction(
    violations: Sequence[LengthViolation], unused_fact_ids: Sequence[str]
) -> Message:
    problems = LINE_SEPARATOR.join(violation_line(violation) for violation in violations)
    unused = (
        UNUSED_FACTS_CLAUSE.format(ids=ID_LIST_SEPARATOR.join(unused_fact_ids))
        if unused_fact_ids
        else ""
    )
    return Message(
        role=Role.USER,
        content=EXPAND_CORRECTION_TEMPLATE.format(problems=problems, unused=unused),
    )


def render_short_correction(
    actual: int, limit: int, sentences: Sequence[str], budget: SentenceBudget
) -> Message:
    return Message(
        role=Role.USER,
        content=SHORT_CORRECTION_TEMPLATE.format(
            actual=actual,
            excess=actual - limit,
            limit=limit,
            sentences=LINE_SEPARATOR.join(
                NAMED_SENTENCE_LINE.format(sentence=sentence) for sentence in sentences
            ),
            max_sentences=budget.max_sentences,
        ),
    )
