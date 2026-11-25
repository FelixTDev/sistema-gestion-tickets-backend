from collections.abc import Generator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket
from app.modules.usuarios.models.user import User
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
        json={"content": "necesito ayuda con este inconveniente particular"},
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


def assert_utc_timestamp(value: str) -> None:
    assert value.endswith("Z") or value.endswith("+00:00")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)


def test_creates_ticket_from_authenticated_unresolved_conversation(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    conversation_id = authenticated_conversation(client, client_token)

    ticket = create_ticket(client, client_token, category_id(engine), conversation_id)

    assert ticket["status"] == "NUEVO"
    assert ticket["source"] == "CHATBOT"
    assert ticket["conversation_id"] == conversation_id
    assert ticket["tracking_code"].startswith("TCK-")
    assert_utc_timestamp(ticket["created_at"])
    assert_utc_timestamp(ticket["updated_at"])

    history = client.get(
        f"/api/v1/tickets/{ticket['id']}/history",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert history.status_code == 200
    actions = [item["action"] for item in history.json()]
    assert actions.index("CREATED") < actions.index("SLA_STARTED")


def test_ticket_get_and_list_serialize_verified_creation_timestamps(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    headers = {"Authorization": f"Bearer {client_token}"}
    ticket = create_ticket(client, client_token, category_id(engine))

    detail = client.get(f"/api/v1/tickets/{ticket['id']}", headers=headers)
    listing = client.get("/api/v1/tickets/mine", headers=headers)

    assert detail.status_code == 200
    assert_utc_timestamp(detail.json()["created_at"])
    assert_utc_timestamp(detail.json()["updated_at"])
    assert listing.status_code == 200
    listed_ticket = next(item for item in listing.json() if item["id"] == ticket["id"])
    assert_utc_timestamp(listed_ticket["created_at"])
    assert_utc_timestamp(listed_ticket["updated_at"])


def test_ticket_get_and_list_keep_ambiguous_creation_timestamps_naive(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    headers = {"Authorization": f"Bearer {client_token}"}
    ticket = create_ticket(client, client_token, category_id(engine))
    ambiguous_created_at = datetime(2001, 2, 3, 4, 5, 6)
    ambiguous_updated_at = datetime(2001, 2, 3, 4, 5, 7)
    with Session(engine) as session:
        row = session.get(Ticket, ticket["id"])
        row.created_at = ambiguous_created_at
        row.updated_at = ambiguous_updated_at
        creation_event = session.exec(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket["id"],
                TicketHistory.action == "CREATED",
            )
        ).one()
        creation_event.created_at = ambiguous_created_at + timedelta(hours=1)
        session.add_all([row, creation_event])
        session.commit()

    detail = client.get(f"/api/v1/tickets/{ticket['id']}", headers=headers)
    listing = client.get("/api/v1/tickets/mine", headers=headers)

    assert detail.status_code == 200
    assert detail.json()["created_at"] == "2001-02-03T04:05:06"
    assert detail.json()["updated_at"] == "2001-02-03T04:05:07"
    assert listing.status_code == 200
    listed_ticket = next(item for item in listing.json() if item["id"] == ticket["id"])
    assert listed_ticket["created_at"] == "2001-02-03T04:05:06"
    assert listed_ticket["updated_at"] == "2001-02-03T04:05:07"
    with Session(engine) as session:
        stored = session.get(Ticket, ticket["id"])
        assert stored.created_at == ambiguous_created_at
        assert stored.updated_at == ambiguous_updated_at


def test_assignment_and_status_timestamps_serialize_verified_utc(ticket_client):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    supervisor_headers = {
        "Authorization": f"Bearer {token(client, 'supervisor@demo.com')}"
    }
    ticket = create_ticket(client, client_token, category_id(engine))
    with Session(engine) as session:
        advisor = session.exec(
            select(User).where(User.email == "asesor@demo.com")
        ).one()

    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers=supervisor_headers,
        json={"advisor_id": advisor.id},
    )
    in_progress = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers=supervisor_headers,
        json={"status": "EN_PROCESO"},
    )
    resolved = client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers=supervisor_headers,
        json={"status": "RESUELTO"},
    )
    closed = client.post(
        f"/api/v1/tickets/{ticket['id']}/confirm-resolution",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert assigned.status_code == 201
    assert_utc_timestamp(assigned.json()["assigned_at"])
    assert in_progress.status_code == resolved.status_code == 200
    assert_utc_timestamp(resolved.json()["resolved_at"])
    assert closed.status_code == 200
    assert_utc_timestamp(closed.json()["closed_at"])

    detail = client.get(f"/api/v1/tickets/{ticket['id']}", headers=supervisor_headers)
    listing = client.get("/api/v1/tickets", headers=supervisor_headers)
    assert detail.status_code == listing.status_code == 200
    for response_ticket in (
        detail.json(),
        next(item for item in listing.json() if item["id"] == ticket["id"]),
    ):
        for field in ("assigned_at", "resolved_at", "closed_at", "updated_at"):
            assert_utc_timestamp(response_ticket[field])

    cancellable_ticket = create_ticket(client, client_token, category_id(engine))
    cancelled = client.post(
        f"/api/v1/tickets/{cancellable_ticket['id']}/cancel",
        headers=supervisor_headers,
        json={"reason": "Cancelación solicitada para probar fecha."},
    )
    assert cancelled.status_code == 200
    assert_utc_timestamp(cancelled.json()["cancelled_at"])


def test_history_uses_explicit_utc_for_known_events_and_preserves_ambiguous_rows(
    ticket_client,
):
    client, engine = ticket_client
    client_token = token(client, "cliente@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    ambiguous_time = datetime(2001, 2, 3, 4, 5, 6)
    with Session(engine) as session:
        session.add(
            TicketHistory(
                ticket_id=ticket["id"],
                actor_id=None,
                action="CREATED",
                old_value=None,
                new_value="NUEVO",
                description="Evento CREATED importado sin procedencia UTC verificable.",
                created_at=ambiguous_time,
            )
        )
        session.add(
            TicketHistory(
                ticket_id=ticket["id"],
                actor_id=None,
                action="LEGACY_IMPORTED",
                old_value=None,
                new_value=None,
                description="Evento histórico sin procedencia horaria verificable.",
                created_at=ambiguous_time,
            )
        )
        session.commit()

    history = client.get(
        f"/api/v1/tickets/{ticket['id']}/history",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert history.status_code == 200
    history_rows = history.json()
    known_rows = {
        action: next(
            item
            for item in history_rows
            if item["action"] == action
            and item["description"]
            in {
                "Ticket creado",
                "SLA iniciado con la creación del ticket",
            }
        )
        for action in ("CREATED", "SLA_STARTED")
    }
    for action in ("CREATED", "SLA_STARTED"):
        value = known_rows[action]["created_at"]
        assert value.endswith("Z") or value.endswith("+00:00")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.utcoffset().total_seconds() == 0
    with Session(engine) as session:
        stored = {
            item.id: item.created_at
            for item in session.exec(
                select(TicketHistory).where(TicketHistory.ticket_id == ticket["id"])
            ).all()
        }
    for action in ("CREATED", "SLA_STARTED"):
        parsed = datetime.fromisoformat(
            known_rows[action]["created_at"].replace("Z", "+00:00")
        )
        assert parsed.replace(tzinfo=None) == stored[known_rows[action]["id"]]
    assert (
        next(
            item["created_at"]
            for item in history_rows
            if item["action"] == "LEGACY_IMPORTED"
        )
        == "2001-02-03T04:05:06"
    )
    ambiguous_created = next(
        item
        for item in history_rows
        if item["action"] == "CREATED"
        and item["description"].startswith("Evento CREATED importado")
    )
    assert ambiguous_created["created_at"] == "2001-02-03T04:05:06"


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
    advisor_id = advisor_login.json()["user"]["id"]
    supervisor_token = token(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert assigned.status_code == 201

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
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    ).json()
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    invalid = transition(client, ticket_id, supervisor_token, "EN_PROCESO")
    assigned_without_advisor = transition(
        client, ticket_id, supervisor_token, "ASIGNADO"
    )

    assert assigned_without_advisor.status_code == 409
    assert (
        client.post(
            f"/api/v1/tickets/{ticket_id}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_login["user"]["id"]},
        ).status_code
        == 201
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
    assert invalid.json()["code"] == "TICKET_ASSIGNMENT_REQUIRED"
    assert "asesor" in invalid.json()["message"].lower()


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
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    ).json()
    ticket = create_ticket(client, client_token, category_id(engine))
    remaining_path = initial_path
    if initial_path:
        assigned = client.post(
            f"/api/v1/tickets/{ticket['id']}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_login["user"]["id"]},
        )
        assert assigned.status_code == 201
        remaining_path = initial_path[1:]
    for next_status in remaining_path:
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
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    ).json()
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    assert (
        client.post(
            f"/api/v1/tickets/{ticket_id}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_login["user"]["id"]},
        ).status_code
        == 201
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
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    ).json()
    ticket = create_ticket(client, client_token, category_id(engine))
    ticket_id = ticket["id"]
    assigned = client.post(
        f"/api/v1/tickets/{ticket_id}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_login["user"]["id"]},
    )
    assert assigned.status_code == 201
    for status in ("EN_PROCESO", "RESUELTO", "CERRADO"):
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
