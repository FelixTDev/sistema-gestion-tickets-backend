from datetime import datetime
from typing import Protocol


class EmailProvider(Protocol):
    """Port for future email delivery without coupling auth to SMTP."""

    def send_password_reset(
        self, email: str, token: str, expires_at: datetime
    ) -> None: ...

    def send_email_verification(
        self, email: str, token: str, expires_at: datetime
    ) -> None: ...


class NoopEmailProvider:
    """Safe local adapter: it never sends, stores, or logs raw tokens."""

    def send_password_reset(self, email: str, token: str, expires_at: datetime) -> None:
        return None

    def send_email_verification(
        self, email: str, token: str, expires_at: datetime
    ) -> None:
        return None
