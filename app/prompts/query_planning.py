from app.config.constants import QUERY_COUNT_MAX, QUERY_COUNT_MIN
from app.domain.llm import Message, Role

QUERY_PLANNING_SYSTEM_PROMPT = (
    "You plan the web research for a Russian-language history post. "
    "The user sends a topic. Produce between {minimum} and {maximum} search queries that "
    "together cover it from different angles: the event or person itself, dates and places, "
    "participants, causes and consequences, how historians assess it. "
    "Write some of the queries in Russian and some in English. "
    "Each query is a short search phrase, not a question to a person. "
    "The queries must differ from one another in meaning and must not be empty. "
    "Do not answer the topic and do not state any facts, only produce the queries."
)
QUERY_PLANNING_USER_TEMPLATE = "Topic: {topic}"


def render_query_planning(topic: str) -> list[Message]:
    return [
        Message(
            role=Role.SYSTEM,
            content=QUERY_PLANNING_SYSTEM_PROMPT.format(
                minimum=QUERY_COUNT_MIN, maximum=QUERY_COUNT_MAX
            ),
        ),
        Message(role=Role.USER, content=QUERY_PLANNING_USER_TEMPLATE.format(topic=topic)),
    ]
