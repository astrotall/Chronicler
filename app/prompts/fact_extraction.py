from collections.abc import Sequence

from app.config.constants import FACT_CANDIDATES_MAX, FACT_SUPPORT_MAX
from app.domain.llm import Message, Role
from app.domain.snippet import Snippet

FACT_EXTRACTION_SYSTEM_PROMPT = (
    "You extract facts for a Russian-language history post. "
    "The user sends a topic and numbered source snippets. Each snippet starts with a label "
    "in square brackets, for example [S1]. "
    "Rules:\n"
    "1. Use only what the snippets say. Do not add anything from your own knowledge, even if "
    "you are sure it is true. The topic only tells you which facts are relevant; it is not "
    "a source.\n"
    "2. Each fact is atomic: one checkable claim (one event, one date, one number, one "
    "relation). Split compound sentences into several facts.\n"
    "3. Write the text of each fact in Russian, close to the wording of the sources. "
    "This holds for snippets in English too: translate the claim into Russian, and keep "
    "only the quote in English. "
    "Write every number and date with digits, exactly as the source writes it: do not "
    "round, convert, add up or reformat numbers, and never write a number in words.\n"
    "4. Every fact has a support list. Each support item names one snippet by its label "
    "exactly as given (for example S3) and a quote: a passage copied character for "
    "character from that snippet, in the language of that snippet. Do not translate, "
    "paraphrase, shorten or join pieces of text, and do not use '...' inside a quote. "
    "A quote is at least {min_quote_chars} characters long and contains every number and "
    "date of the fact.\n"
    "5. When several snippets state the same claim, make one fact with one support item "
    "per snippet, at most {support_max} items. Do not merge different claims into one fact.\n"
    "6. If snippets disagree (different dates, numbers or causes), keep each version as "
    "its own fact with its own support.\n"
    "7. Return at most {candidates_max} facts, the most relevant to the topic first. "
    "If the snippets hold nothing relevant, return an empty list."
)
FACT_EXTRACTION_USER_TEMPLATE = "Topic: {topic}\n\nSnippets:\n\n{snippets}"
SNIPPET_TEMPLATE = "[{alias}] {title}\n{text}"
SNIPPET_SEPARATOR = "\n\n"

DISPUTE_CHECK_SYSTEM_PROMPT = (
    "You check a list of facts about one historical topic for contradictions. "
    "Each line starts with the id of a fact.\n"
    "A contradiction is two or more facts that make claims about the same thing (the same "
    "event, the same quantity, the same attribute of the same person) and cannot all be "
    "true at once: two different dates of one event, two different numbers for one "
    "quantity, two different places, causes or outcomes of one event.\n"
    "These are NOT contradictions:\n"
    "- facts about different events, people or moments in time;\n"
    "- a sequence of events, even when a later event reverses an earlier one (a title "
    "given, then taken away, then returned);\n"
    "- one fact being more precise or more detailed than another (a range and a year "
    "inside it, a name and the same name with a nickname);\n"
    "- a fact that mentions something another fact does not mention;\n"
    "- a suspicion or a version stated next to the plain fact it is about.\n"
    "When in doubt, do not report a contradiction: a false report makes the author "
    "distrust a sound fact.\n"
    "For each candidate group give the ids exactly as given, then a short explanation in "
    "Russian of what exactly the facts disagree about, for the author of the post, then "
    "the verdict: contradiction is true only if the facts really cannot all be true by the "
    "rules above, otherwise false. Do not mention the ids in the explanation, describe the "
    "claims instead. Use only ids from the list. If there are no contradictions, return an "
    "empty list."
)
DISPUTE_CHECK_USER_TEMPLATE = "Facts:\n{facts}"
FACT_LINE_TEMPLATE = "{fact_id}: {text}"
FACT_LINE_SEPARATOR = "\n"


def render_fact_extraction(
    topic: str, snippets: Sequence[tuple[str, Snippet]], min_quote_chars: int
) -> list[Message]:
    snippet_blocks = SNIPPET_SEPARATOR.join(
        SNIPPET_TEMPLATE.format(alias=alias, title=snippet.title, text=snippet.text)
        for alias, snippet in snippets
    )
    return [
        Message(
            role=Role.SYSTEM,
            content=FACT_EXTRACTION_SYSTEM_PROMPT.format(
                min_quote_chars=min_quote_chars,
                support_max=FACT_SUPPORT_MAX,
                candidates_max=FACT_CANDIDATES_MAX,
            ),
        ),
        Message(
            role=Role.USER,
            content=FACT_EXTRACTION_USER_TEMPLATE.format(topic=topic, snippets=snippet_blocks),
        ),
    ]


def render_dispute_check(facts: Sequence[tuple[str, str]]) -> list[Message]:
    lines = FACT_LINE_SEPARATOR.join(
        FACT_LINE_TEMPLATE.format(fact_id=fact_id, text=text) for fact_id, text in facts
    )
    return [
        Message(role=Role.SYSTEM, content=DISPUTE_CHECK_SYSTEM_PROMPT),
        Message(role=Role.USER, content=DISPUTE_CHECK_USER_TEMPLATE.format(facts=lines)),
    ]
