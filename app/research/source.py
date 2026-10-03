from typing import Protocol

from app.domain.snippet import Snippet


class ResearchSource(Protocol):
    name: str

    async def search(self, query: str) -> list[Snippet]: ...
