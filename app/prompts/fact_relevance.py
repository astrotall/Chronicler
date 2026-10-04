from collections.abc import Sequence

from app.config.constants import RELEVANCE_MAX, RELEVANCE_MAX_ASPECTS, RELEVANCE_MIN
from app.domain.llm import Message, Role

RELEVANCE_CHECK_SYSTEM_PROMPT = (
    "You rank facts for a Russian-language history post by how well they answer its topic. "
    "The user sends the topic and a list of facts already checked against their sources. "
    "Each line starts with the id of a fact. The topic only says what the post is about; it "
    "is not a source. Do not check, correct, add or rewrite facts, and do not use your own "
    "knowledge: judge only by the text of each fact against the topic.\n"
    "For every fact of the list return exactly one entry with these fields, in this order:\n"
    "- id: exactly as given, for example C3.\n"
    "- aspect: which part of the topic the fact is about, in 1 to 3 Russian words, for "
    "example «жильё», «питание», «рабочее время», «причины», «ход сражения», «последствия». "
    "Facts about the same part get the same aspect, word for word. Use at most "
    "{max_aspects} different aspects for the whole list.\n"
    "- about_source: true only when the fact is about a source and not about the subject: "
    "a page or a site, an exhibition or an exposition, a book or a publication, an author or "
    "a researcher (who they are, how well known they are, that the subject is topical or "
    "worth studying, what the materials help to see). Examples of about_source: «На выставке "
    "представлены фотографии и плакаты эпохи», «Исследовательница одной из первых начала "
    "изучать эту тему», «Изучение быта этого периода особенно актуально». "
    "A fact about the name, the definition or the history of the subject itself is NOT "
    "about_source and is judged by relevance like any other fact. Examples that are NOT "
    "about_source: «Термин для этого события впервые употребил историк XIX века», «Сражение "
    "также называют по имени реки». A fact that names who reports or estimates something "
    "about the subject (historians, a chronicle, a life of a saint, a document) is about the "
    "subject, and so is a fact about how the subject is remembered: «По оценкам историков, "
    "войско насчитывало около 30 тысяч человек», «Летопись сообщает, что перед походом князь "
    "получил благословение», «Памятный день сражения отмечают ежегодно».\n"
    "- outside_period: true only when the topic names a period (a year, a decade, a century, "
    "an era) and the fact is about a time clearly outside it. A fact dated inside the "
    "period is never outside it. Direct causes and direct consequences of what happened in "
    "the period are not outside it. If the topic names no period, outside_period is always "
    "false.\n"
    "- relevance: an integer from {minimum} to {maximum}, given last. {maximum}: the fact "
    "answers the topic directly. 2: useful context for the topic. 1: marginal, or it "
    "repeats a fuller fact of the list. {minimum}: not about the topic, or the text states no "
    "claim at all (a single word such as «Опровергнуто», a heading, a fragment of a "
    "sentence).\n"
    "Every id of the list gets exactly one entry; use only ids from the list."
)
RELEVANCE_CHECK_USER_TEMPLATE = "Topic: {topic}\n\nFacts:\n{facts}"
RELEVANCE_LINE_TEMPLATE = "{fact_id}: {text}"
RELEVANCE_LINE_SEPARATOR = "\n"


def render_relevance_check(topic: str, facts: Sequence[tuple[str, str]]) -> list[Message]:
    lines = RELEVANCE_LINE_SEPARATOR.join(
        RELEVANCE_LINE_TEMPLATE.format(fact_id=fact_id, text=text) for fact_id, text in facts
    )
    return [
        Message(
            role=Role.SYSTEM,
            content=RELEVANCE_CHECK_SYSTEM_PROMPT.format(
                max_aspects=RELEVANCE_MAX_ASPECTS, minimum=RELEVANCE_MIN, maximum=RELEVANCE_MAX
            ),
        ),
        Message(
            role=Role.USER,
            content=RELEVANCE_CHECK_USER_TEMPLATE.format(topic=topic, facts=lines),
        ),
    ]
