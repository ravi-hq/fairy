class SessionServiceError(Exception):
    """Base for all session-service failures."""


class NoSpritesKeyError(SessionServiceError):
    """Caller has not configured a Sprites API key."""


class ProvisionError(SessionServiceError):
    """Sprites rejected a provision / prepare / write operation.

    `stage` identifies which setup step failed and is for server-side logging
    only — it must never be sent to API clients.
    """

    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage


class SessionHandleNotFound(SessionServiceError):
    """The backing Sprite is no longer available."""
