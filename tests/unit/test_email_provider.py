from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.modules.usuarios.services import email_provider as email_module

SECURE_KEY = "a-secure-test-secret-key-with-more-than-32-characters"


class FakeSmtpTransport:
    instances: list["FakeSmtpTransport"] = []
    transport = "smtp"

    def __init__(
        self, host: str, port: int, *, timeout: int, context: object | None = None
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.started_tls_with = None
        self.login_credentials: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []
        self.closed = False
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeSmtpTransport":
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True

    def starttls(self, *, context) -> None:
        self.started_tls_with = context

    def login(self, username: str, password: str) -> None:
        self.login_credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


class FakeSmtpSslTransport(FakeSmtpTransport):
    instances: list[FakeSmtpTransport] = []
    transport = "smtp_ssl"


@pytest.fixture(autouse=True)
def reset_fake_transports():
    FakeSmtpTransport.instances.clear()
    FakeSmtpSslTransport.instances.clear()


def smtp_settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "email_provider": "smtp",
        "smtp_host": "smtp.example.test",
        "smtp_port": 2525,
        "smtp_from_email": "no-reply@example.com",
        "smtp_use_tls": False,
        "smtp_starttls": True,
        "smtp_timeout_seconds": 9,
    }
    values.update(overrides)
    return Settings(**values)


def test_email_provider_configuration_is_strict_by_environment():
    assert hasattr(email_module, "build_email_provider")
    assert isinstance(
        email_module.build_email_provider(
            Settings(app_env="test", email_provider="noop")
        ),
        email_module.NoopEmailProvider,
    )
    with pytest.raises(ValueError):
        Settings(
            app_env="staging",
            email_provider="noop",
            secret_key=SECURE_KEY,
        )
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            email_provider="smtp",
            smtp_host=None,
            smtp_from_email=None,
            secret_key=SECURE_KEY,
        )
    with pytest.raises(ValueError):
        smtp_settings(smtp_username="mailer", smtp_password=None)
    with pytest.raises(ValueError):
        smtp_settings(smtp_use_tls=True, smtp_starttls=True)
    with pytest.raises(ValueError):
        smtp_settings(smtp_port=0)
    with pytest.raises(ValueError):
        smtp_settings(smtp_timeout_seconds=61)


def test_debug_mode_is_rejected_outside_local_environments():
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(
            app_env="production",
            debug=True,
            email_provider="smtp",
            smtp_host="smtp.example.com",
            smtp_from_email="no-reply@example.com",
            secret_key=SECURE_KEY,
        )

    settings = Settings(
        app_env="production",
        debug=False,
        email_provider="smtp",
        smtp_host="smtp.example.com",
        smtp_from_email="no-reply@example.com",
        secret_key=SECURE_KEY,
    )

    assert settings.debug is False


def test_plain_smtp_is_rejected_in_staging_and_production():
    for environment in ("staging", "production"):
        with pytest.raises(ValueError, match="TLS|STARTTLS"):
            Settings(
                app_env=environment,
                debug=False,
                email_provider="smtp",
                smtp_host="smtp.example.com",
                smtp_from_email="no-reply@example.com",
                smtp_use_tls=False,
                smtp_starttls=False,
                secret_key=SECURE_KEY,
            )


def test_smtp_starttls_transport_sends_token_only_in_body(monkeypatch, caplog):
    tls_context = object()
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSmtpTransport)
    monkeypatch.setattr(email_module.ssl, "create_default_context", lambda: tls_context)
    settings = smtp_settings(
        smtp_username="mailer-user",
        smtp_password=SecretStr("smtp-password"),
    )
    provider = email_module.build_email_provider(settings)
    expires_at = datetime.now(UTC) + timedelta(minutes=30)
    token = "password-reset-token-secret"

    provider.send_password_reset("client@example.com", token, expires_at)

    assert isinstance(provider, email_module.SmtpEmailProvider)
    transport = FakeSmtpTransport.instances[0]
    assert (transport.host, transport.port, transport.timeout) == (
        "smtp.example.test",
        2525,
        9,
    )
    assert transport.started_tls_with is tls_context
    assert transport.login_credentials == ("mailer-user", "smtp-password")
    assert transport.closed is True
    message = transport.messages[0]
    assert message["From"] == "no-reply@example.com"
    assert message["To"] == "client@example.com"
    assert token in message.get_body().get_content()
    assert token not in str(message.items())
    assert expires_at.isoformat() in message.get_body().get_content()
    assert token not in caplog.text
    assert "smtp-password" not in caplog.text
    assert "smtp-password" not in repr(settings)


def test_smtp_ssl_transport_supports_anonymous_server(monkeypatch):
    tls_context = object()
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSmtpSslTransport)
    monkeypatch.setattr(email_module.ssl, "create_default_context", lambda: tls_context)
    settings = smtp_settings(
        smtp_use_tls=True,
        smtp_starttls=False,
        smtp_username=None,
        smtp_password=None,
    )
    provider = email_module.build_email_provider(settings)

    provider.send_email_verification(
        "client@example.com",
        "verification-token-secret",
        datetime.now(UTC) + timedelta(hours=24),
    )

    transport = FakeSmtpSslTransport.instances[0]
    assert transport.transport == "smtp_ssl"
    assert transport.context is tls_context
    assert transport.started_tls_with is None
    assert transport.login_credentials is None
    assert "verification-token-secret" in transport.messages[0].get_body().get_content()
