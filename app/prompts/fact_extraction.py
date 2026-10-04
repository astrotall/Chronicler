from collections.abc import Sequence

from app.config.constants import FACT_CANDIDATES_MAX, FACT_SUPPORT_MAX
from app.domain.fact import ClaimStance
from app.domain.llm import Message, Role
from app.domain.snippet import Snippet

FACT_EXTRACTION_SYSTEM_PROMPT = (
    "You extract facts for a Russian-language history post. "
    "The user sends a topic and numbered source snippets. Each snippet starts with a label "
    "in square brackets, for example [S1]. "
    "Return at most {candidates_max} facts in total. Code keeps only the first "
    "{candidates_max} and the rest are lost, so choose the most relevant ones.\n"
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
    "7. Every fact has a stance: how the snippet itself presents the claim. Give the field "
    "stance only for a claimed or a rebutted fact; leave it out for an asserted one.\n"
    "- asserted: the snippet states it as true. This is the default and by far the most "
    "common stance. A claim the snippet reports plainly is asserted even if it cites a "
    "document, a count or a measurement it relies on and does not doubt (по данным "
    "переписи, согласно указу, по статистике).\n"
    "- claimed: the snippet distances itself from the claim: it gives it as a legend or "
    "a tradition (по преданию, согласно легенде, as a chronicle tells it), as a common "
    "belief or a myth, as what many or some sources say, as the version of some "
    "historians, or with якобы, будто бы, allegedly.\n"
    "- rebutted: the snippet gives the claim and then rebuts it (однако, на самом деле, "
    "in fact).\n"
    "The words that show a claimed or rebutted stance must be in the quote or in the "
    "sentence right before or after it in the snippet. Never mark a fact claimed or "
    "rebutted because of what you know yourself, or because the claim sounds doubtful: "
    "only the snippet's own wording counts.\n"
    "8. A claimed or rebutted fact is never written as a plain statement. Its text keeps "
    "the attribution in Russian: «По преданию, ...», «По распространённому утверждению, "
    "...», «Некоторые источники утверждают, что ...». A rebutted fact also has a rebuttal: "
    "the rebutting statement as its own text with its own support, written as a plain "
    "fact (for example «Документы эпохи упоминают крепость только с 1520 года.»). Do not "
    "repeat the rebuttal as a separate fact, and give a rebuttal only for a rebutted fact. If "
    "one snippet presents a claim as claimed or rebutted and another states it plainly, "
    "it is still one fact with the support of both and the more cautious stance.\n"
    "9. Return at most {candidates_max} facts, the most relevant to the topic first; never "
    "more than {candidates_max}. If the snippets hold nothing relevant, return an empty list."
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
    "A claim that something was (an event, a date, a number, a thing) and a claim that it "
    "was not, or that it is refuted, are a contradiction, also when one of them carries an "
    "attribution such as «по утверждению источника», «по преданию» or «по версии»: the "
    "attribution does not remove the contradiction. Compare the lines marked [claimed] with "
    "the plain facts and with the denials too. Examples:\n"
    "- «По утверждению источника, мост через реку построили в 1900 году» and «Моста через "
    "реку в 1900 году не строили»: a contradiction.\n"
    "- «По преданию, у ворот стояла сторожевая башня» and «Раскопки показали, что башни у "
    "ворот не было»: a contradiction.\n"
    "These are NOT contradictions:\n"
    "- facts about different events, people or moments in time;\n"
    "- a sequence of events, even when a later event reverses an earlier one (a title "
    "given, then taken away, then returned);\n"
    "- one fact being more precise or more detailed than another (a range and a year "
    "inside it, a name and the same name with a nickname, a rounded number and the exact "
    "one such as «около 300» and «312»);\n"
    "- a fact that mentions something another fact does not mention;\n"
    "- a suspicion or a version that adds to the plain fact it is about without denying "
    "it. A line marked [claimed] is a claim a source attributes to others (a legend, a "
    "version); it contradicts a plain fact only when one says the thing was and the other "
    "says it was not, or when they give different values of it.\n"
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
STANCE_FACT_LINE_TEMPLATE = "{fact_id} [{stance}]: {text}"
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


def dispute_line(fact_id: str, text: str, stance: ClaimStance) -> str:
    if stance is ClaimStance.ASSERTED:
        return FACT_LINE_TEMPLATE.format(fact_id=fact_id, text=text)
    return STANCE_FACT_LINE_TEMPLATE.format(fact_id=fact_id, stance=stance.value, text=text)


def render_dispute_check(facts: Sequence[tuple[str, str, ClaimStance]]) -> list[Message]:
    lines = FACT_LINE_SEPARATOR.join(
        dispute_line(fact_id, text, stance) for fact_id, text, stance in facts
    )
    return [
        Message(role=Role.SYSTEM, content=DISPUTE_CHECK_SYSTEM_PROMPT),
        Message(role=Role.USER, content=DISPUTE_CHECK_USER_TEMPLATE.format(facts=lines)),
    ]
