from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.bot.messages import TOPIC_FORMAT_PREFIXES, TOPIC_PREFIX_SEPARATOR
from app.config.constants import TOPIC_MAX_CHARS
from app.domain.draft import PostFormat


class TopicRejection(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"


class TopicRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str
    post_format: PostFormat | None = None


def split_prefix(text: str) -> tuple[PostFormat | None, str]:
    head, separator, rest = text.partition(TOPIC_PREFIX_SEPARATOR)
    if separator:
        chosen = TOPIC_FORMAT_PREFIXES.get(head.strip().casefold())
        if chosen is not None:
            return chosen, rest.strip()
    return None, text


def parse_topic(text: str) -> TopicRequest | TopicRejection:
    post_format, topic = split_prefix(text.strip())
    if not topic:
        return TopicRejection.EMPTY
    if len(topic) > TOPIC_MAX_CHARS:
        return TopicRejection.TOO_LONG
    return TopicRequest(topic=topic, post_format=post_format)
