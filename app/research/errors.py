class SourceError(Exception):
    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source}: {reason}")


class SourceAuthError(SourceError):
    pass


class SourceRequestError(SourceError):
    pass


class SourceRateLimitError(SourceError):
    pass


class SourceUnavailableError(SourceError):
    pass


class SourceInvalidResponseError(SourceError):
    pass
