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
from app.modules.tickets.models.comment import TicketComment
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


def login(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        return session.exec(select(TicketCategory)).first().id


def create_ticket(client: TestClient, engine: object) -> tuple[dict, str]:
    client_token = login(client, "cliente@demo.com")
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {client_token}"},
        json={
            "category_id": category_id(engine),
            "subject": "Consulta de comentarios",
            "description": "Ticket usado para probar la consulta de comentarios.",
            "priority": "MEDIA",
        },
    )
    assert response.status_code == 201
    return response.json(), client_token


def add_comment(client: TestClient, ticket_id: str, client_token: str, content: str):
    response = client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"content": content},
    )
    assert response.status_code == 201
    return response.json()


def test_owner_gets_comments_in_ascending_created_at_order(ticket_client):
    client, engine = ticket_client
    ticket, client_token = create_ticket(client, engine)
    first = add_comment(client, ticket["id"], client_token, "Primer comentario")
    second = add_comment(client, ticket["id"], client_token, "Segundo comentario")
    now = datetime.now(UTC)
    with Session(engine) as session:
        first_model = session.get(TicketComment, first["id"])
        second_model = session.get(TicketComment, second["id"])
        first_model.created_at = now + timedelta(minutes=1)
        second_model.created_at = now
        session.commit()

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert response.status_code == 200
    assert [item["content"] for item in response.json()] == [
        "Segundo comentario",
        "Primer comentario",
    ]
    assert set(response.json()[0]) == {
        "id",
        "ticket_id",
        "author_id",
        "content",
        "created_at",
    }
    assert "password_hash" not in response.text
    assert "access_token" not in response.text


def test_owner_gets_empty_list_when_ticket_has_no_comments(ticket_client):
    client, engine = ticket_client
    ticket, client_token = create_ticket(client, engine)

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_non_owner_client_is_forbidden(ticket_client):
    client, engine = ticket_client
    ticket, _ = create_ticket(client, engine)
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cliente alterno",
            "email": "comments.other@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registration.status_code == 201
    other_token = login(client, "comments.other@example.com", "Demo-password-123")

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("email", ["asesor@demo.com", "supervisor@demo.com"])
def test_internal_authorized_users_get_comments(ticket_client, email: str):
    client, engine = ticket_client
    ticket, client_token = create_ticket(client, engine)
    add_comment(client, ticket["id"], client_token, "Comentario visible al equipo")
    if email == "asesor@demo.com":
        advisor_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "demo-password-local"},
        )
        internal_token = advisor_login.json()["access_token"]
        advisor_id = advisor_login.json()["user"]["id"]
        supervisor_token = login(client, "supervisor@demo.com")
        assigned = client.post(
            f"/api/v1/tickets/{ticket['id']}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_id},
        )
        assert assigned.status_code == 201
    else:
        internal_token = login(client, email)

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {internal_token}"},
    )

    assert response.status_code == 200
    assert response.json()[0]["content"] == "Comentario visible al equipo"


def test_comments_require_authentication(ticket_client):
    client, engine = ticket_client
    ticket, _ = create_ticket(client, engine)

    response = client.get(f"/api/v1/tickets/{ticket['id']}/comments")

    assert response.status_code == 401


def test_missing_ticket_returns_not_found(ticket_client):
    client, _ = ticket_client
    client_token = login(client, "cliente@demo.com")

    response = client.get(
        "/api/v1/tickets/missing-ticket/comments",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert response.status_code == 404
