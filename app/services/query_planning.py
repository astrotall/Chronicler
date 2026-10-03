from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.config.constants import QUERY_COUNT_MAX, QUERY_COUNT_MIN, QUERY_PLANNING_MAX_TOKENS
from app.llm.client import LLMClient
from app.prompts.query_planning import render_query_planning

type SearchQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class QueryPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    queries: list[SearchQuery] = Field(min_length=QUERY_COUNT_MIN, max_length=QUERY_COUNT_MAX)

    @field_validator("queries")
    @classmethod
    def require_unique(cls, queries: list[str]) -> list[str]:
        if len({query.casefold() for query in queries}) != len(queries):
            raise ValueError("queries must be unique")
        return queries


async def plan_queries(client: LLMClient, topic: str) -> list[str]:
    if not topic.strip():
        raise ValueError("topic must not be blank")
    plan = await client.complete_json(
        render_query_planning(topic.strip()), QueryPlan, max_tokens=QUERY_PLANNING_MAX_TOKENS
    )
    return list(plan.queries)
