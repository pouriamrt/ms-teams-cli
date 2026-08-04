"""Exception hierarchy and exit-code mapping."""

from __future__ import annotations


class TeamsError(Exception):
    """Base for all CLI errors. Subclasses set `exit_code`."""

    exit_code: int = 1


class SessionExpired(TeamsError):
    """Refresh token revoked/expired — user must re-login."""

    exit_code = 77


class NotFound(TeamsError):
    """An index, email, or ID could not be resolved."""

    exit_code = 64


class UserError(TeamsError):
    """User-side flag/usage error (bad emoji, bad flag combo)."""

    exit_code = 2


class ApiError(TeamsError):
    """Wraps a non-retryable HTTP error from Graph or chatsvc."""

    exit_code = 1

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def __repr__(self) -> str:
        sc = f" status={self.status_code}" if self.status_code is not None else ""
        return f"ApiError({self.args[0]!r}{sc})"
