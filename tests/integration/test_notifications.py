from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.usuarios.models.user import User
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def notification_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def create_ticket(client: TestClient, token: str, category: str) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "subject": "Consulta para notificaciones",
            "description": "Descripción para validar eventos in-app.",
            "priority": "MEDIA",
        },
    )
    assert response.status_code == 201
    return response.json()


def advisor_credentials(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    assert response.status_code == 200
    return response.json()["access_token"], response.json()["user"]["id"]


def list_notifications(client: TestClient, token: str, **params: object) -> dict:
    response = client.get(
        "/api/v1/notifications",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"page", "page_size", "total", "total_pages", "items"}
    return body


def assign_ticket(
    client: TestClient, ticket_id: str, supervisor_token: str, advisor_id: str
) -> None:
    response = client.post(
        f"/api/v1/tickets/{ticket_id}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert response.status_code == 201


def test_creating_ticket_persists_in_app_notification(notification_client):
    client, engine = notification_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))

    body = list_notifications(client, token)

    assert body["total"] == 1
    assert body["items"][0]["type"] == "ticket_created"
    assert body["items"][0]["related_ticket_id"] == ticket["id"]
    assert "password_hash" not in str(body)
    assert "access_token" not in str(body)


def test_assignment_creates_notification_for_assigned_advisor(notification_client):
    client, engine = notification_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_token, advisor_id = advisor_credentials(client)
    ticket = create_ticket(client, client_token, category_id(engine))
    assign_ticket(client, ticket["id"], supervisor_token, advisor_id)

    body = list_notifications(client, advisor_token)

    assert body["total"] == 1
    assert body["items"][0]["type"] == "ticket_assigned"
    assert body["items"][0]["related_ticket_id"] == ticket["id"]


def test_comment_creates_notification_for_ticket_counterpart(notification_client):
    client, engine = notification_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_token, advisor_id = advisor_credentials(client)
    ticket = create_ticket(client, client_token, category_id(engine))
    assign_ticket(client, ticket["id"], supervisor_token, advisor_id)

    comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Comentario interno para el cliente"},
    )
    assert comment.status_code == 201

    body = list_notifications(client, client_token)

    assert [item["type"] for item in body["items"]].count("ticket_commented") == 1


def test_status_close_and_reopen_create_notifications(notification_client):
    client, engine = notification_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    _, advisor_id = advisor_credentials(client)
    ticket = create_ticket(client, client_token, category_id(engine))
    assign_ticket(client, ticket["id"], supervisor_token, advisor_id)

    for status in ("EN_PROCESO", "RESUELTO"):
        response = client.post(
            f"/api/v1/tickets/{ticket['id']}/status",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"status": status},
        )
        assert response.status_code == 200

    reopened = client.post(
        f"/api/v1/tickets/{ticket['id']}/reopen",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Se requiere información adicional"},
    )
    assert reopened.status_code == 200
    for status in ("RESUELTO",):
        response = client.post(
            f"/api/v1/tickets/{ticket['id']}/status",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"status": status},
        )
        assert response.status_code == 200
    closed = client.post(
        f"/api/v1/tickets/{ticket['id']}/close",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    assert closed.status_code == 200

    types = [item["type"] for item in list_notifications(client, client_token)["items"]]
    assert types.count("ticket_status_changed") == 3
    assert types.count("ticket_reopened") == 1
    assert types.count("ticket_closed") == 1


def test_password_change_creates_notification(notification_client):
    client, _ = notification_client
    token = login(client, "cliente@demo.com")
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "demo-password-local",
            "new_password": "New-demo-password-123",
        },
    )
    assert changed.status_code == 200
    new_token = login(client, "cliente@demo.com", "New-demo-password-123")

    body = list_notifications(client, new_token)

    assert [item["type"] for item in body["items"]] == ["password_changed"]


def test_notification_creation_is_idempotent_and_provider_failure_is_non_blocking(
    notification_client,
):
    _, engine = notification_client
    from app.modules.notificaciones.services.notification_service import (
        NotificationService,
    )

    class FailingProvider:
        def publish(self, delivery) -> None:
            raise RuntimeError("provider unavailable")

    with Session(engine) as session:
        user = session.exec(select(User)).first()
        assert user is not None
        service = NotificationService(provider=FailingProvider())
        first = service.create(
            session,
            recipient_user_id=user.id,
            notification_type="security_event",
            title="Evento de seguridad",
            message="Se registró un evento de seguridad.",
            metadata={"event": "test"},
            dedupe_key="security:test:1",
        )
        second = service.create(
            session,
            recipient_user_id=user.id,
            notification_type="security_event",
            title="Evento de seguridad",
            message="Se registró un evento de seguridad.",
            metadata={"event": "test"},
            dedupe_key="security:test:1",
        )
        session.commit()

        assert first.id == second.id


def test_notifications_are_paginated_descending_and_count_unread(notification_client):
    client, engine = notification_client
    token = login(client, "cliente@demo.com")
    category = category_id(engine)
    create_ticket(client, token, category)
    create_ticket(client, token, category)

    first_page = list_notifications(client, token, page=1, page_size=1)
    second_page = list_notifications(client, token, page=2, page_size=1)
    unread = client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert first_page["total"] == 2
    assert first_page["total_pages"] == 2
    assert len(first_page["items"]) == 1
    assert len(second_page["items"]) == 1
    assert first_page["items"][0]["created_at"] >= second_page["items"][0]["created_at"]
    assert unread.status_code == 200
    assert unread.json() == {"unread_count": 2}


def test_mark_one_and_all_notifications_as_read(notification_client):
    client, engine = notification_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))
    body = list_notifications(client, token)
    notification_id = body["items"][0]["id"]

    marked = client.patch(
        f"/api/v1/notifications/{notification_id}/read",
        headers={"Authorization": f"Bearer {token}"},
    )
    count_after_one = client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {token}"},
    )
    all_read = client.post(
        "/api/v1/notifications/read-all",
        headers={"Authorization": f"Bearer {token}"},
    )
    count_after_all = client.get(
        "/api/v1/notifications/unread-count",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert marked.status_code == 200
    assert marked.json()["is_read"] is True
    assert marked.json()["read_at"] is not None
    assert count_after_one.json() == {"unread_count": 0}
    assert all_read.status_code == 200
    assert all_read.json() == {"updated_count": 0}
    assert count_after_all.json() == {"unread_count": 0}
    assert ticket["id"] == body["items"][0]["related_ticket_id"]


def test_notification_isolation_and_http_errors(notification_client):
    client, engine = notification_client
    owner_token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, owner_token, category_id(engine))
    notification = list_notifications(client, owner_token)["items"][0]
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Otro usuario",
            "email": "notification.other@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_token = login(client, "notification.other@example.com", "Demo-password-123")

    own_other_list = list_notifications(client, other_token)
    forbidden = client.patch(
        f"/api/v1/notifications/{notification['id']}/read",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    missing = client.patch(
        "/api/v1/notifications/missing-notification/read",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    invalid = client.get(
        "/api/v1/notifications",
        params={"page": 0, "page_size": 101},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    unauthenticated = client.get("/api/v1/notifications")

    assert own_other_list["items"] == []
    assert forbidden.status_code == 403
    assert missing.status_code == 404
    assert invalid.status_code == 422
    assert unauthenticated.status_code == 401
    assert ticket["id"] == notification["related_ticket_id"]


def test_metadata_rejects_sensitive_keys(notification_client):
    _, engine = notification_client
    from app.modules.notificaciones.services.notification_service import (
        NotificationService,
    )

    with Session(engine) as session:
        user = session.exec(select(User)).first()
        assert user is not None
        service = NotificationService()
        with pytest.raises(ValueError, match="sensible"):
            service.create(
                session,
                recipient_user_id=user.id,
                notification_type="security_event",
                title="Evento",
                message="Mensaje",
                metadata={"password": "no debe persistirse"},
                dedupe_key="security:invalid-metadata",
            )
