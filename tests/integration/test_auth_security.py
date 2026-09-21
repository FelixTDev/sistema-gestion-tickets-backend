from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import Settings, get_settings
from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.usuarios.api.auth_router import get_email_provider
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def security_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


class CapturingEmailProvider:
    def __init__(self) -> None:
        self.password_reset_tokens: dict[str, str] = {}
        self.verification_tokens: dict[str, str] = {}

    def send_password_reset(self, email, token, expires_at) -> None:
        self.password_reset_tokens[email] = token

    def send_email_verification(self, email, token, expires_at) -> None:
        self.verification_tokens[email] = token


def test_forgot_password_does_not_enumerate_accounts(security_client: TestClient):
    existing = security_client.post(
        "/api/v1/auth/forgot-password", json={"email": "cliente@demo.com"}
    )
    missing = security_client.post(
        "/api/v1/auth/forgot-password", json={"email": "missing@example.com"}
    )

    assert existing.status_code == 202
    assert missing.status_code == 202
    assert existing.json() == missing.json()
    assert "token" not in existing.text.casefold()


def test_security_headers_and_trusted_hosts_are_enforced(
    security_client: TestClient,
):
    response = security_client.get("/api/v1/health")
    untrusted_host = security_client.get(
        "/api/v1/health", headers={"host": "evil.example"}
    )

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert untrusted_host.status_code == 400


def test_change_password_requires_authentication(security_client: TestClient):
    response = security_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "demo-password-local", "new_password": "Nueva-1234"},
    )

    assert response.status_code == 401


def test_change_password_revokes_old_session_and_allows_new_login(
    security_client: TestClient,
):
    old_token = login(security_client, "cliente@demo.com")
    changed = security_client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {old_token}"},
        json={"current_password": "demo-password-local", "new_password": "Nueva-1234"},
    )

    assert changed.status_code == 200
    assert (
        security_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}
        ).status_code
        == 401
    )
    assert (
        security_client.post(
            "/api/v1/auth/login",
            json={"email": "cliente@demo.com", "password": "Nueva-1234"},
        ).status_code
        == 200
    )


def test_change_password_invalidates_pending_password_reset_tokens(
    security_client: TestClient,
):
    provider = CapturingEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: provider
    requested = security_client.post(
        "/api/v1/auth/forgot-password", json={"email": "cliente@demo.com"}
    )
    access_token = login(security_client, "cliente@demo.com")
    changed = security_client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"current_password": "demo-password-local", "new_password": "Cambio-1234"},
    )
    reset_after_change = security_client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": provider.password_reset_tokens["cliente@demo.com"],
            "new_password": "Reset-1234",
        },
    )

    assert requested.status_code == 202
    assert changed.status_code == 200
    assert reset_after_change.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"token": "invalid-token", "new_password": "Nueva-1234"},
        {"token": "", "new_password": "Nueva-1234"},
    ],
)
def test_reset_password_rejects_invalid_tokens(
    security_client: TestClient, payload: dict[str, str]
):
    response = security_client.post("/api/v1/auth/reset-password", json=payload)

    assert response.status_code == 400
    assert "token" not in response.json()["detail"].casefold()


def test_reset_password_rejects_invalid_password_policy(
    security_client: TestClient,
):
    response = security_client.post(
        "/api/v1/auth/reset-password",
        json={"token": "opaque-token", "new_password": "short"},
    )

    assert response.status_code == 422


def test_change_password_rejects_invalid_password_policy(
    security_client: TestClient,
):
    access_token = login(security_client, "cliente@demo.com")
    response = security_client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"current_password": "demo-password-local", "new_password": "short"},
    )

    assert response.status_code == 422


def test_verify_email_rejects_invalid_token_without_echoing_it(
    security_client: TestClient,
):
    response = security_client.post(
        "/api/v1/auth/verify-email", json={"token": "invalid-token"}
    )

    assert response.status_code == 400
    assert "invalid-token" not in response.text


def test_verify_email_rejects_invalid_token_shape(
    security_client: TestClient,
):
    response = security_client.post(
        "/api/v1/auth/verify-email", json={"token": "x" * 257}
    )

    assert response.status_code == 422


def test_password_reset_token_is_one_time_and_invalidates_sessions(
    security_client: TestClient,
):
    provider = CapturingEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: provider
    old_token = login(security_client, "cliente@demo.com")
    second_token = login(security_client, "cliente@demo.com")

    first_requested = security_client.post(
        "/api/v1/auth/forgot-password", json={"email": "cliente@demo.com"}
    )
    first_raw_token = provider.password_reset_tokens["cliente@demo.com"]
    second_requested = security_client.post(
        "/api/v1/auth/forgot-password", json={"email": "cliente@demo.com"}
    )
    second_raw_token = provider.password_reset_tokens["cliente@demo.com"]
    reset = security_client.post(
        "/api/v1/auth/reset-password",
        json={"token": second_raw_token, "new_password": "Reset-1234"},
    )
    reused = security_client.post(
        "/api/v1/auth/reset-password",
        json={"token": second_raw_token, "new_password": "Reset-5678"},
    )
    invalidated = security_client.post(
        "/api/v1/auth/reset-password",
        json={"token": first_raw_token, "new_password": "Reset-5678"},
    )

    assert first_requested.status_code == 202
    assert second_requested.status_code == 202
    assert first_raw_token not in first_requested.text
    assert second_raw_token not in second_requested.text
    assert reset.status_code == 200
    assert reused.status_code == 400
    assert invalidated.status_code == 400
    assert (
        security_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"}
        ).status_code
        == 401
    )
    assert (
        security_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {second_token}"}
        ).status_code
        == 401
    )
    assert (
        security_client.post(
            "/api/v1/auth/login",
            json={"email": "cliente@demo.com", "password": "Reset-1234"},
        ).status_code
        == 200
    )


def test_expired_password_reset_token_is_rejected(security_client: TestClient):
    provider = CapturingEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: provider
    settings = get_settings()
    original_expiration = settings.password_reset_token_expire_minutes
    settings.password_reset_token_expire_minutes = 0
    try:
        security_client.post(
            "/api/v1/auth/forgot-password", json={"email": "cliente@demo.com"}
        )
        response = security_client.post(
            "/api/v1/auth/reset-password",
            json={
                "token": provider.password_reset_tokens["cliente@demo.com"],
                "new_password": "Reset-1234",
            },
        )
    finally:
        settings.password_reset_token_expire_minutes = original_expiration

    assert response.status_code == 400


def test_registered_user_can_verify_email_once_without_exposing_token(
    security_client: TestClient,
):
    provider = CapturingEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: provider
    registration = security_client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Verificación Cliente",
            "email": "verification@example.com",
            "password": "Seguro-1234",
        },
    )
    raw_token = provider.verification_tokens["verification@example.com"]
    verified = security_client.post(
        "/api/v1/auth/verify-email", json={"token": raw_token}
    )
    reused = security_client.post(
        "/api/v1/auth/verify-email", json={"token": raw_token}
    )

    assert registration.status_code == 201
    assert raw_token not in registration.text
    assert verified.status_code == 200
    assert reused.status_code == 400


def test_logout_revokes_current_session(security_client: TestClient):
    access_token = login(security_client, "cliente@demo.com")
    logged_out = security_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert logged_out.status_code == 200
    assert (
        security_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        ).status_code
        == 401
    )


def test_failed_logins_are_rate_limited_without_new_error_details(
    security_client: TestClient,
):
    for _ in range(6):
        response = security_client.post(
            "/api/v1/auth/login",
            json={"email": "cliente@demo.com", "password": "Incorrecta-123"},
        )
        assert response.status_code == 401

    blocked = security_client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@demo.com", "password": "demo-password-local"},
    )

    assert blocked.status_code == 401
    assert "rate" not in blocked.text.casefold()


def test_password_policy_uses_configured_minimum(security_client: TestClient):
    settings = get_settings()
    original_minimum = settings.password_min_length
    settings.password_min_length = 12
    try:
        response = security_client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Política Cliente",
                "email": "policy@example.com",
                "password": "Corta-123",
            },
        )
    finally:
        settings.password_min_length = original_minimum

    assert response.status_code == 422


def test_production_rejects_weak_secret_and_non_hs256_algorithm():
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="change-this-secret")

    with pytest.raises(ValueError):
        Settings(jwt_algorithm="RS256")
