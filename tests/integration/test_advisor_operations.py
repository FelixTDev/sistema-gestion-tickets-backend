from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.notificaciones.models.notification import Notification
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.sla import TicketSla
from app.modules.usuarios.models.role import Role
from app.modules.usuarios.models.user import User
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def operations_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "demo-password-local"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def create_ticket(client: TestClient, token: str, category: str, subject: str) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "subject": subject,
            "description": f"Descripción operativa de {subject}.",
            "priority": "URGENTE",
        },
    )
    assert response.status_code == 201
    return response.json()


def add_advisor(engine: object, email: str, *, is_active: bool = True) -> User:
    with Session(engine) as session:
        role = session.exec(select(Role).where(Role.name == "ASESOR")).one()
        user = User(
            full_name=email.split("@")[0],
            email=email,
            password_hash="not-returned",
            role_id=role.id,
            is_active=is_active,
            email_verified=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def assign(client: TestClient, token: str, ticket_id: str, advisor_id: str):
    return client.post(
        f"/api/v1/tickets/{ticket_id}/assignments",
        headers={"Authorization": f"Bearer {token}"},
        json={"advisor_id": advisor_id},
    )


def queue(client: TestClient, token: str, queue_name: str, **params: object) -> dict:
    response = client.get(
        "/api/v1/tickets/operations",
        params={"queue": queue_name, "page": 1, "page_size": 20, **params},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def test_assigned_unassigned_and_supervisor_specific_queues_are_paginated(
    operations_client,
):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]
    category = category_id(engine)
    assigned = create_ticket(client, client_token, category, "Asignado operativo")
    unassigned = create_ticket(client, client_token, category, "Cola operativa")
    assert (
        assign(client, supervisor_token, assigned["id"], advisor_id).status_code == 201
    )

    assigned_body = queue(client, advisor_token, "assigned_to_me")
    unassigned_body = queue(client, advisor_token, "unassigned")
    supervisor_body = queue(
        client, supervisor_token, "assigned_to_advisor", advisor_id=advisor_id
    )

    assert [item["id"] for item in assigned_body["items"]] == [assigned["id"]]
    assert [item["id"] for item in unassigned_body["items"]] == [unassigned["id"]]
    assert [item["id"] for item in supervisor_body["items"]] == [assigned["id"]]
    assert set(assigned_body) == {"page", "page_size", "total", "total_pages", "items"}


def test_operational_queues_filter_sla_and_recent_updates(operations_client):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    soon = create_ticket(client, client_token, category, "SLA próximo")
    overdue = create_ticket(client, client_token, category, "SLA vencido")
    now = datetime.now(UTC)
    with Session(engine) as session:
        soon_sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == soon["id"])
        ).one()
        overdue_sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == overdue["id"])
        ).one()
        soon_sla.first_response_due_at = now + timedelta(minutes=1)
        overdue_sla.first_response_due_at = now - timedelta(minutes=1)
        session.add(soon_sla)
        session.add(overdue_sla)
        session.commit()

    soon_body = queue(client, supervisor_token, "sla_soon")
    overdue_body = queue(client, supervisor_token, "sla_overdue")
    pending_body = queue(client, supervisor_token, "pending_first_response")
    recent_body = queue(client, supervisor_token, "recently_updated", recent_hours=24)

    assert [item["id"] for item in soon_body["items"]] == [soon["id"]]
    assert [item["id"] for item in overdue_body["items"]] == [overdue["id"]]
    assert {soon["id"], overdue["id"]}.issubset(
        {item["id"] for item in pending_body["items"]}
    )
    assert {soon["id"], overdue["id"]}.issubset(
        {item["id"] for item in recent_body["items"]}
    )


def test_take_release_and_duplicate_assignment_are_audited_and_notified(
    operations_client,
):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]
    ticket = create_ticket(client, client_token, category_id(engine), "Tomar ticket")

    taken = client.post(
        f"/api/v1/tickets/{ticket['id']}/take",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    duplicate_take = client.post(
        f"/api/v1/tickets/{ticket['id']}/take",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    released = client.post(
        f"/api/v1/tickets/{ticket['id']}/release",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )

    assert taken.status_code == 200
    assert taken.json()["assigned_advisor_id"] == advisor_id
    assert duplicate_take.status_code == 409
    assert released.status_code == 200
    assert released.json()["assigned_advisor_id"] is None
    with Session(engine) as session:
        history = session.exec(
            select(TicketHistory).where(TicketHistory.ticket_id == ticket["id"])
        ).all()
        notifications = session.exec(
            select(Notification).where(Notification.related_ticket_id == ticket["id"])
        ).all()
    assert {row.action for row in history} >= {"TAKEN", "RELEASED"}
    assert any(
        item.notification_type.value == "ticket_assigned" for item in notifications
    )
    assert supervisor_token


def test_supervisor_reassigns_and_rejects_invalid_assignees(operations_client):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    client_id = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login(client, 'cliente@demo.com')}"},
    ).json()["id"]
    first = add_advisor(engine, "first.operations@example.com")
    second = add_advisor(engine, "second.operations@example.com")
    inactive = add_advisor(engine, "inactive.operations@example.com", is_active=False)
    ticket = create_ticket(client, client_token, category_id(engine), "Reasignación")

    assigned = assign(client, supervisor_token, ticket["id"], first.id)
    duplicate = assign(client, supervisor_token, ticket["id"], first.id)
    reassigned = assign(client, supervisor_token, ticket["id"], second.id)
    inactive_response = assign(client, supervisor_token, ticket["id"], inactive.id)
    client_response = assign(client, supervisor_token, ticket["id"], client_id)

    assert assigned.status_code == 201
    assert duplicate.status_code == 409
    assert reassigned.status_code == 201
    assert reassigned.json()["assigned_advisor_id"] == second.id
    assert inactive_response.status_code == 422
    assert client_response.status_code == 422


def test_advisor_cannot_operate_unassigned_or_other_tickets(operations_client):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine), "Sin asesor")

    read = client.get(
        f"/api/v1/tickets/{ticket['id']}",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    status_change = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "EN_PROCESO"},
    )
    comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "No autorizado"},
    )

    assert read.status_code == 403
    assert status_change.status_code == 403
    assert comment.status_code == 403


def test_advisor_transitions_assigned_ticket_and_terminal_state_is_protected(
    operations_client,
):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]
    ticket = create_ticket(client, client_token, category_id(engine), "Estados asesor")
    assert assign(client, supervisor_token, ticket["id"], advisor_id).status_code == 201

    in_process = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "EN_PROCESO"},
    )
    resolved = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "RESUELTO"},
    )
    closed = client.post(
        f"/api/v1/tickets/{ticket['id']}/close",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    invalid_after_close = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "EN_PROCESO"},
    )

    assert in_process.status_code == 200
    assert resolved.status_code == 200
    assert closed.status_code == 200
    assert invalid_after_close.status_code == 409


def test_advisor_cannot_cancel_through_generic_status_endpoint(operations_client):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]
    ticket = create_ticket(client, client_token, category_id(engine), "Cancelación")
    assert assign(client, supervisor_token, ticket["id"], advisor_id).status_code == 201

    response = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"status": "CANCELADO", "reason": "Motivo de prueba"},
    )

    assert response.status_code == 403


def test_expected_version_returns_conflict_without_losing_current_update(
    operations_client,
):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine), "Concurrencia")
    current = client.get(
        f"/api/v1/tickets/{ticket['id']}",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    ).json()

    with Session(engine) as session:
        row = session.get(User, ticket["client_id"])
        assert row is not None
        stored_ticket = session.get(models.Ticket, ticket["id"])
        assert stored_ticket is not None
        stored_ticket.version += 1
        session.add(stored_ticket)
        session.commit()

    conflict = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"status": "ASIGNADO", "expected_version": current["version"]},
    )

    assert conflict.status_code == 409


def test_operational_routes_require_roles_and_validate_requests(operations_client):
    client, engine = operations_client
    client_token = login(client, "cliente@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    create_ticket(client, client_token, category_id(engine), "Validación")

    unauthenticated = client.get(
        "/api/v1/tickets/operations", params={"queue": "unassigned"}
    )
    client_forbidden = client.get(
        "/api/v1/tickets/operations",
        params={"queue": "assigned_to_me"},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    invalid_queue = client.get(
        "/api/v1/tickets/operations",
        params={"queue": "unknown"},
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    missing = client.post(
        "/api/v1/tickets/missing-ticket/take",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )

    assert unauthenticated.status_code == 401
    assert client_forbidden.status_code == 403
    assert invalid_queue.status_code == 422
    assert missing.status_code == 404
