from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.history import TicketHistory
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def ticket_client() -> Generator[tuple[TestClient, object], None, None]:
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


def token(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        return session.exec(select(TicketCategory)).first().id


def authenticated_conversation(client: TestClient, client_token: str) -> str:
    conversation = client.post(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert conversation.status_code == 201
    unknown = client.post(
        f"/api/v1/chat/conversations/{conversation.json()['id']}/messages",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"content": "consulta no resuelta para abrir ticket"},
    )
    assert unknown.status_code == 200
    assert unknown.json()["resolved"] is False
    return conversation.json()["id"]


def create_ticket(
    client: TestClient,
    client_token: str,
    category: str,
    conversation_id: str | None = None,
) -> dict:
    payload = {
        "category_id": category,
        "subject": "Ayuda con mi solicitud",
        "description": "Necesito orientación sobre una consulta no resuelta.",
        "priority": "MEDIA",
    }
    if conversation_id is not None:
        response = client.post(
            f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
            headers={"Authorization": f"Bearer {client_token}"},
            json=payload,
        )
    else:
        response = client.post(
            "/api/v1/tickets",
            headers={"Authorization": f"Bearer {client_token}"},
            json=payload,
        )
    assert response.status_code == 201
    return response.json()


def test_creates_ticket_from_authenticated_unresolved_conversation(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    conversation_id = authenticated_conversation(client, client_token)

    ticket = create_ticket(client, client_token, category_id(engine), conversation_id)

    assert ticket["status"] == "NUEVO"
    assert ticket["source"] == "CHATBOT"
    assert ticket["conversation_id"] == conversation_id
    assert ticket["tracking_code"].startswith("TCK-")


def test_rejects_ticket_creation_without_authentication(ticket_client):
    client, engine = ticket_client
    response = client.post(
        "/api/v1/tickets",
        json={
            "category_id": category_id(engine),
            "subject": "Sin autenticación",
            "description": "No debe crearse",
            "priority": "BAJA",
        },
    )

    assert response.status_code == 401


def test_manual_tickets_have_unique_tracking_codes(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    first = create_ticket(client, client_token, category_id(engine))
    second = create_ticket(client, client_token, category_id(engine))

    assert first["source"] == "MANUAL"
    assert first["tracking_code"] != second["tracking_code"]


def test_client_lists_only_own_tickets_and_cannot_read_another_client(ticket_client):
    client, engine = ticket_client
    owner_token = token(client, "cliente@demo.com")
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Otro cliente",
            "email": "otro.ticket@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_token = token(client, "otro.ticket@example.com", "Demo-password-123")
    ticket = create_ticket(client, owner_token, category_id(engine))

    mine = client.get(
        "/api/v1/tickets/mine",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    other = client.get(
        f"/api/v1/tickets/{ticket['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert mine.status_code == 200
    assert any(item["id"] == ticket["id"] for item in mine.json())
    assert other.status_code == 403


def test_advisor_lists_tickets_and_supervisor_lists_all(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    advisor_token = advisor_login.json()["access_token"]
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))

    advisor_list = client.get(
        "/api/v1/tickets", headers={"Authorization": f"Bearer {advisor_token}"}
    )
    supervisor_list = client.get(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    assert advisor_list.status_code == 200
    assert supervisor_list.status_code == 200
    assert any(item["id"] == ticket["id"] for item in advisor_list.json())
    assert any(item["id"] == ticket["id"] for item in supervisor_list.json())


def test_only_supervisor_can_assign_ticket(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    advisor_token = advisor_login.json()["access_token"]
    advisor_id = advisor_login.json()["user"]["id"]
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))

    forbidden = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )

    assert forbidden.status_code == 403
    assert assigned.status_code == 201
    assert assigned.json()["status"] == "ASIGNADO"


def transition(
    client, ticket_id: str, current_token: str, new_status: str, reason=None
):
    payload = {"status": new_status}
    if reason is not None:
        payload["reason"] = reason
    return client.post(
        f"/api/v1/tickets/{ticket_id}/status",
        headers={"Authorization": f"Bearer {current_token}"},
        json=payload,
    )


def test_all_allowed_transitions_and_invalid_transition(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    invalid = transition(client, ticket_id, supervisor_token, "EN_PROCESO")

    assert (
        transition(client, ticket_id, supervisor_token, "ASIGNADO").status_code == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "EN_PROCESO").status_code == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "PENDIENTE_CLIENTE").status_code
        == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "EN_PROCESO").status_code == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "RESUELTO").status_code == 200
    )
    assert (
        transition(
            client, ticket_id, supervisor_token, "EN_PROCESO", "Requiere revisión"
        ).status_code
        == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "RESUELTO").status_code == 200
    )
    assert transition(client, ticket_id, supervisor_token, "CERRADO").status_code == 200
    assert invalid.status_code == 409
    assert "transición" in invalid.json()["detail"].lower()


@pytest.mark.parametrize(
    "initial_path",
    [
        (),
        ("ASIGNADO",),
        ("ASIGNADO", "EN_PROCESO"),
        ("ASIGNADO", "EN_PROCESO", "RESUELTO"),
    ],
)
def test_cancellation_is_allowed_from_open_states(ticket_client, initial_path):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    for next_status in initial_path:
        assert (
            transition(client, ticket["id"], supervisor_token, next_status).status_code
            == 200
        )

    cancelled = transition(
        client, ticket["id"], supervisor_token, "CANCELADO", "Solicitud cancelada"
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELADO"


def test_comments_follow_permissions_and_history_is_recorded(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))

    comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"content": "Añado información adicional."},
    )
    history = client.get(
        f"/api/v1/tickets/{ticket['id']}/history",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    assert comment.status_code == 201
    assert history.status_code == 200
    assert any(item["action"] == "COMMENT_ADDED" for item in history.json())


def test_cancel_requires_reason_and_is_recorded(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))

    missing_reason = client.post(
        f"/api/v1/tickets/{ticket['id']}/cancel",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={},
    )
    cancelled = client.post(
        f"/api/v1/tickets/{ticket['id']}/cancel",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Solicitud duplicada"},
    )

    assert missing_reason.status_code == 422
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELADO"


def test_reopen_resolved_ticket_requires_reason(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    assert (
        transition(client, ticket_id, supervisor_token, "ASIGNADO").status_code == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "EN_PROCESO").status_code == 200
    )
    assert (
        transition(client, ticket_id, supervisor_token, "RESUELTO").status_code == 200
    )

    reopened = client.post(
        f"/api/v1/tickets/{ticket_id}/reopen",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "El cliente reporta que persiste el problema"},
    )

    assert reopened.status_code == 200
    assert reopened.json()["status"] == "EN_PROCESO"


def test_closed_ticket_cannot_be_modified_directly(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    for status in ("ASIGNADO", "EN_PROCESO", "RESUELTO", "CERRADO"):
        assert (
            transition(client, ticket_id, supervisor_token, status).status_code == 200
        )

    comment = client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"content": "Cambio posterior"},
    )
    status_change = transition(client, ticket_id, supervisor_token, "EN_PROCESO")

    assert comment.status_code == 409
    assert status_change.status_code == 409


def test_history_rows_contain_actor_and_old_new_values(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    transition(client, ticket["id"], supervisor_token, "CANCELADO", "No procede")

    with Session(engine) as session:
        rows = session.exec(
            select(TicketHistory).where(TicketHistory.ticket_id == ticket["id"])
        ).all()

    assert any(
        row.actor_id is not None
        and row.old_value == "NUEVO"
        and row.new_value == "CANCELADO"
        for row in rows
    )
