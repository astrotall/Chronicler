from collections.abc import Sequence

from app.config.style import FILLER_CLOSER_EXAMPLES, OPINION_MAX_PER_POST
from app.domain.fact import FactSet
from app.domain.llm import Message, Role
from app.domain.style import Violation
from app.prompts.style_rules import LIST_SEPARATOR, quoted, render_style_rules
from app.prompts.writing import (
    BLOCK_SEPARATOR,
    DISPUTES_BLOCK,
    LINE_SEPARATOR,
    assertable_facts,
    attributed_block,
    dispute_blocks,
    disputed_ids,
    fact_lines,
    previous_text,
)

CRITIC_SYSTEM_PROMPT = (
    "You review a Russian post written for the author's history account on X. The post may "
    "state only what the facts the user sends state, and it follows the style rules at the "
    "end of this message. Find the defects of the kinds below and nothing else.\n"
    "\n"
    "Kinds of defects:\n"
    "- unsupported_claim: a statement that no fact states. This includes a conclusion, a "
    "cause or a consequence, a claim of importance or of what an event changed or did not "
    "change («Победа на Дону не отменила ни ордынской силы»); an added qualifier that changes "
    "the standing of a fact («По преданию», «по легенде», «говорят» when the fact states the "
    "event plainly and is not among the attributed claims); an added precision or emphasis "
    "that no fact gives («Место известно точно», «Уже в 1382 году» adds the meaning 'soon "
    "after', «всего», «сразу»); an interval or a count computed from the facts («через два "
    "года»); a disputed fact stated as established; an attributed claim (claimed or "
    "rebutted) stated as a fact, without its attribution («Пересвет вышел на поединок с "
    "Челубеем» when the fact is [claimed] «По преданию, перед битвой Пересвет бился с "
    "Челубеем»); a rebutted claim mentioned without its rebuttal.\n"
    "- ambiguous_reference: a pronoun or an omitted subject whose reference is unclear, so "
    "that the sentence can be read as a claim the facts do not state. Example: «Сошлись "
    "войска. Вёл их князь Дмитрий» reads as if Dmitry led both armies, while the facts say "
    "he led the Russian one.\n"
    "- filler: a sentence whose only job is to restate, comment on or rate the sentence "
    "before it; it can be removed and nothing is lost. Examples: {fillers}, «Это решило "
    "многое.»\n"
    "- opinion: a first-person opinion beyond {opinion_max} in the whole post or thread, or "
    "an opinion used as the closing line of a part.\n"
    "- cliche: a stock phrase or a template move of machine-written text: a solemn "
    "generalisation («история знает много примеров», «навсегда вошёл в историю»), pathos, "
    "a 'not just X but Y' frame in any wording.\n"
    "- triplet: a rhetorical triplet: three parallel adjectives, clauses or abstractions in "
    "a row used for rhythm. A list of names, objects or events taken from the facts is not a "
    "triplet («Армстронг, Олдрин и Коллинз» is fine).\n"
    "- invented_experience: the author claims to have seen, visited or witnessed something.\n"
    "\n"
    "These are not defects, never report them:\n"
    "- an honest retelling of a fact in other words: shorter, with another word order or "
    "other synonyms, as long as it claims nothing the fact does not claim («Перед битвой "
    "Пересвет бился с Челубеем» for the fact «Перед сражением состоялся поединок инока "
    "Пересвета с ордынским богатырём Челубеем»);\n"
    "- two facts put next to each other without a stated cause or consequence;\n"
    "- cautious wording for a disputed fact («по одним данным ..., по другим ...», "
    "«источники расходятся»);\n"
    "- the attribution of an attributed claim («По преданию, ...», «Принято считать, что "
    "...», «Часто пишут, что ...; на деле ...»): such a claim must carry it;\n"
    "- punctuation, dashes, emoji, hashtags, a closing question, the length and the digits "
    "of numbers: code checks them;\n"
    "- the opening hook, the rhythm of sentences and the structure of a thread: they are not "
    "part of this review.\n"
    "\n"
    "For every finding give, in this order: excerpt, the exact words from the post copied "
    "character for character, the shortest piece that shows the problem (a phrase or one "
    "sentence); rule, one of the kinds above; explanation, in Russian, for the author, one "
    "or two sentences on what is wrong, and for unsupported_claim which fact it goes beyond; "
    "violation, true only if this really is a defect by the rules above, false if it turns "
    "out to be an honest retelling or allowed. Report at most {max_findings} findings, the "
    "most serious first. If the post has no defects, return an empty list.\n"
    "\n"
    "{style_rules}"
)
CRITIC_FACTS_BLOCK = "Facts:\n{facts}"
CRITIC_POST_BLOCK = "Post:\n{post}"
CRITIC_THREAD_BLOCK = "Thread, each tweet starts with its number in square brackets:\n{post}"

REVISION_TEMPLATE = (
    "Fix only the flagged fragments below. The rest of the text stays word for word, the same "
    "length, the same facts, the same order. Do not add facts or claims. Where a sentence "
    "adds a meaning that no fact states, remove that meaning or the sentence, and nothing "
    "else.\n"
    "Problems:\n{problems}"
)
FORBIDDEN_TEMPLATE = "\nDo not use these phrases or close variants of them: {phrases}."
PROBLEM_WITH_PART_TEMPLATE = "- part {part}: «{excerpt}»: {explanation}"
PROBLEM_WITH_EXCERPT_TEMPLATE = "- «{excerpt}»: {explanation}"
PROBLEM_TEMPLATE = "- {explanation}"

DASH_EXPLANATION = (
    "Длинное или среднее тире ({dash}). Где нужно тире, ставится дефис с пробелами "
    "(«{replacement}»)."
)
BANNED_PHRASE_EXPLANATION = "Запрещённый оборот: «{phrase}»."
INVENTED_EXPERIENCE_EXPLANATION = "Выдуманный личный опыт: «{phrase}»."
EMOJI_EXPLANATION = "Эмодзи в посте запрещены."
HASHTAG_EXPLANATION = "Хештеги в посте запрещены."
CLOSING_QUESTION_EXPLANATION = "Пост заканчивается вопросом к читателю."
PART_TOO_LONG_EXPLANATION = "Часть {part}: {actual} знаков при пределе {limit}."
TOO_MANY_PARTS_EXPLANATION = "{actual} твитов при максимуме {limit}."
TOO_SHORT_EXPLANATION = "Пост короче минимума: {actual} знаков при минимуме {limit}."
TOO_FEW_FACTS_EXPLANATION = "В посте {actual} фактов при минимуме {limit}."
TOO_FEW_PARTS_EXPLANATION = "{actual} твитов при минимуме {limit}."
REGRESSION_NOTE = (
    "\nThe previous attempt removed too much: keep all the text and all the facts, and change "
    "only the flagged fragments."
)
UNVERIFIED_NUMBER_EXPLANATION = "Числа {number} нет ни в одном факте."


def render_critique(texts: Sequence[str], fact_set: FactSet, max_findings: int) -> list[Message]:
    disputed = disputed_ids(fact_set)
    assertable = assertable_facts(fact_set)
    blocks: list[str] = []
    if assertable:
        blocks.append(CRITIC_FACTS_BLOCK.format(facts=fact_lines(assertable)))
    attributed = attributed_block(fact_set)
    if attributed is not None:
        blocks.append(attributed)
    disputes = dispute_blocks(fact_set, disputed)
    if disputes:
        blocks.append(DISPUTES_BLOCK.format(disputes=BLOCK_SEPARATOR.join(disputes)))
    post_block = CRITIC_POST_BLOCK if len(texts) == 1 else CRITIC_THREAD_BLOCK
    blocks.append(post_block.format(post=previous_text(texts)))
    return [
        Message(
            role=Role.SYSTEM,
            content=CRITIC_SYSTEM_PROMPT.format(
                fillers=quoted(FILLER_CLOSER_EXAMPLES, LIST_SEPARATOR),
                opinion_max=OPINION_MAX_PER_POST,
                max_findings=max_findings,
                style_rules=render_style_rules(),
            ),
        ),
        Message(role=Role.USER, content=BLOCK_SEPARATOR.join(blocks)),
    ]


def problem_line(violation: Violation) -> str:
    if violation.excerpt is None:
        return PROBLEM_TEMPLATE.format(explanation=violation.explanation)
    if violation.part is None:
        return PROBLEM_WITH_EXCERPT_TEMPLATE.format(
            excerpt=violation.excerpt, explanation=violation.explanation
        )
    return PROBLEM_WITH_PART_TEMPLATE.format(
        part=violation.part, excerpt=violation.excerpt, explanation=violation.explanation
    )


def render_style_revision(
    violations: Sequence[Violation], forbidden: Sequence[str], *, after_regression: bool = False
) -> str:
    instruction = REVISION_TEMPLATE.format(
        problems=LINE_SEPARATOR.join(problem_line(violation) for violation in violations)
    )
    if forbidden:
        instruction += FORBIDDEN_TEMPLATE.format(phrases=quoted(forbidden, LIST_SEPARATOR))
    if after_regression:
        instruction += REGRESSION_NOTE
    return instruction
