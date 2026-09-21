from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.notificaciones.models.notification import Notification
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.usuarios.models.user import User
from app.modules.usuarios.models.user_preference import UserPreference
from app.modules.usuarios.models.user_profile_audit import UserProfileAudit
from app.seed.demo_data import seed_demo_data


def login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "demo-password-local"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def profile_client() -> Generator[tuple[TestClient, object], None, None]:
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
        yield client, engine
    app.dependency_overrides.clear()


def test_profile_requires_authentication_and_returns_safe_fields(profile_client):
    client, _ = profile_client

    response = client.get("/api/v1/users/me/profile")

    assert response.status_code == 401

    token = login(client, "cliente@demo.com")
    response = client.get("/api/v1/users/me/profile", headers=auth_header(token))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "cliente@demo.com"
    assert body["role"] == "CLIENTE"
    assert "password_hash" not in body
    assert "sessions_invalidated_at" not in body
    assert "role_id" not in body


def test_profile_update_normalizes_phone_and_audits_change(profile_client):
    client, engine = profile_client
    token = login(client, "cliente@demo.com")

    response = client.patch(
        "/api/v1/users/me/profile",
        headers=auth_header(token),
        json={"full_name": "  Cliente Actualizado  ", "phone": "+51 (999) 111-222"},
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Cliente Actualizado"
    assert response.json()["phone"] == "+51999111222"
    with Session(engine) as session:
        audits = session.exec(select(UserProfileAudit)).all()
        assert {audit.field_name for audit in audits} == {"full_name", "phone"}
        assert all("999111222" not in audit.new_value_redacted for audit in audits)
        notifications = session.exec(select(Notification)).all()
        assert [item.notification_type.value for item in notifications] == [
            "security_event"
        ]


def test_profile_rejects_protected_and_invalid_fields(profile_client):
    client, _ = profile_client
    token = login(client, "cliente@demo.com")

    protected = client.patch(
        "/api/v1/users/me/profile",
        headers=auth_header(token),
        json={"email": "new@example.com", "role": "SUPERVISOR"},
    )
    invalid_phone = client.patch(
        "/api/v1/users/me/profile",
        headers=auth_header(token),
        json={"phone": "not-a-phone"},
    )

    assert protected.status_code == 422
    assert invalid_phone.status_code == 422


def test_preferences_have_safe_defaults_and_can_be_updated(profile_client):
    client, _ = profile_client
    token = login(client, "cliente@demo.com")

    defaults = client.get("/api/v1/users/me/preferences", headers=auth_header(token))
    updated = client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_header(token),
        json={
            "in_app_enabled": False,
            "email_enabled": False,
            "assignment_enabled": False,
            "status_change_enabled": True,
            "comment_enabled": False,
            "sla_enabled": False,
            "preferred_language": "en",
            "timezone": "America/Lima",
        },
    )

    assert defaults.status_code == 200
    assert defaults.json() == {
        "in_app_enabled": True,
        "email_enabled": True,
        "assignment_enabled": True,
        "status_change_enabled": True,
        "comment_enabled": True,
        "sla_enabled": True,
        "preferred_language": "es",
        "timezone": "UTC",
        "security_events_enabled": True,
    }
    assert updated.status_code == 200
    assert updated.json()["in_app_enabled"] is False
    assert updated.json()["timezone"] == "America/Lima"
    assert updated.json()["security_events_enabled"] is True


def test_preferences_reject_invalid_timezone_language_and_security_disable(
    profile_client,
):
    client, _ = profile_client
    token = login(client, "cliente@demo.com")

    invalid_timezone = client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_header(token),
        json={"timezone": "Not/A-Timezone"},
    )
    invalid_language = client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_header(token),
        json={"preferred_language": "xx"},
    )
    disable_security = client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_header(token),
        json={"security_events_enabled": False},
    )

    assert invalid_timezone.status_code == 422
    assert invalid_language.status_code == 422
    assert disable_security.status_code == 422


def test_profile_and_preferences_are_isolated_to_authenticated_user(profile_client):
    client, _ = profile_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")

    client_profile = client.get(
        "/api/v1/users/me/profile", headers=auth_header(client_token)
    )
    advisor_profile = client.get(
        "/api/v1/users/me/profile", headers=auth_header(advisor_token)
    )
    client_update = client.patch(
        "/api/v1/users/me/profile",
        headers=auth_header(client_token),
        json={"full_name": "Cliente Propio"},
    )

    assert client_profile.json()["id"] != advisor_profile.json()["id"]
    assert client_update.status_code == 200
    assert client_update.json()["id"] == client_profile.json()["id"]


def test_notification_preferences_suppress_ordinary_events_but_not_security(
    profile_client,
):
    client, engine = profile_client
    token = login(client, "cliente@demo.com")
    profile_update = client.patch(
        "/api/v1/users/me/preferences",
        headers=auth_header(token),
        json={"in_app_enabled": False, "assignment_enabled": False},
    )
    assert profile_update.status_code == 200

    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        preference = session.exec(
            select(UserPreference).where(UserPreference.user_id == user.id)
        ).one()
        service = NotificationService()
        service.create(
            session,
            recipient_user_id=preference.user_id,
            notification_type="ticket_assigned",
            title="Asignación",
            message="No debe persistirse con in-app desactivado.",
            dedupe_key="preference:test:assignment",
        )
        service.create(
            session,
            recipient_user_id=preference.user_id,
            notification_type="security_event",
            title="Seguridad",
            message="Siempre debe persistirse.",
            dedupe_key="preference:test:security",
        )
        session.commit()
        notifications = session.exec(select(Notification)).all()

    assert [item.notification_type.value for item in notifications].count(
        "ticket_assigned"
    ) == 0
    assert [item.notification_type.value for item in notifications].count(
        "security_event"
    ) == 2


def test_email_change_is_rejected_until_safe_verification_flow_exists(profile_client):
    client, _ = profile_client
    token = login(client, "cliente@demo.com")

    response = client.patch(
        "/api/v1/users/me/profile",
        headers=auth_header(token),
        json={"email": "new@example.com"},
    )

    assert response.status_code == 422
