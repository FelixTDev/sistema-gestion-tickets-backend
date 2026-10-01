from collections.abc import Mapping


class DomainError(Exception):
    """Base exception for domain-level errors."""


class AppError(DomainError):
    """Safe application error with a stable public API code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: list[dict[str, object]] | dict[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = dict(headers or {})
