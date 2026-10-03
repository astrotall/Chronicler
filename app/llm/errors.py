from app.llm.target import CallTarget


class LLMError(Exception):
    def __init__(self, reason: str, target: CallTarget | None = None) -> None:
        self.reason = reason
        self.target = target
        super().__init__(f"{target.describe()}: {reason}" if target else reason)


class LLMConfigError(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


class LLMRequestError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMInvalidResponseError(LLMError):
    pass
