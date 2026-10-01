import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings


class EmailProvider(Protocol):
    """Port for email delivery without coupling auth to SMTP."""

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


class SmtpEmailProvider:
    """SMTP adapter that keeps credentials and one-time tokens out of logs."""

    def __init__(self, settings: Settings) -> None:
        self.host = settings.smtp_host or ""
        self.port = settings.smtp_port
        self.username = settings.smtp_username
        self.password = settings.smtp_password
        self.from_email = settings.smtp_from_email or ""
        self.use_tls = settings.smtp_use_tls
        self.starttls = settings.smtp_starttls
        self.timeout = settings.smtp_timeout_seconds

    def send_password_reset(self, email: str, token: str, expires_at: datetime) -> None:
        self._send(
            recipient=email,
            subject="Recuperación de contraseña",
            body=(
                "Usa el siguiente token para restablecer tu contraseña:\n\n"
                f"{token}\n\n"
                f"Expira en: {expires_at.isoformat()}"
            ),
        )

    def send_email_verification(
        self, email: str, token: str, expires_at: datetime
    ) -> None:
        self._send(
            recipient=email,
            subject="Verificación de correo electrónico",
            body=(
                "Usa el siguiente token para verificar tu correo:\n\n"
                f"{token}\n\n"
                f"Expira en: {expires_at.isoformat()}"
            ),
        )

    def _send(self, *, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        tls_context = (
            ssl.create_default_context() if self.use_tls or self.starttls else None
        )
        if self.use_tls:
            transport_context = smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=self.timeout,
                context=tls_context,
            )
        else:
            transport_context = smtplib.SMTP(
                self.host,
                self.port,
                timeout=self.timeout,
            )
        with transport_context as transport:
            if self.starttls:
                transport.starttls(context=tls_context)
            if self.username is not None and self.password is not None:
                transport.login(self.username, self.password.get_secret_value())
            transport.send_message(message)


def build_email_provider(settings: Settings) -> EmailProvider:
    if settings.email_provider == "smtp":
        return SmtpEmailProvider(settings)
    return NoopEmailProvider()
