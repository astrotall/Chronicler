from pydantic import BaseModel, ConfigDict

from app.config.constants import LLMProvider, LLMStep


class CallTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: LLMProvider
    model: str
    step: LLMStep

    def describe(self) -> str:
        return f"step {self.step} on {self.provider} ({self.model})"
