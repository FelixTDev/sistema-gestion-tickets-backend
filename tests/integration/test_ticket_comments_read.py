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
from app.modules.usuarios.models.role import Role
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
        "author_name",
        "author_role",
        "content",
        "created_at",
    }
    assert "password_hash" not in response.text
    assert "access_token" not in response.text


def test_comments_and_history_include_visible_actor_identity(ticket_client):
    client, engine = ticket_client
    ticket, client_token = create_ticket(client, engine)
    advisor_token = login(client, "asesor@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]
    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert assigned.status_code == 201

    client_comment = add_comment(
        client, ticket["id"], client_token, "Comentario del cliente"
    )
    advisor_comment_response = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Comentario del asesor"},
    )
    supervisor_comment_response = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"content": "Comentario del supervisor"},
    )
    assert advisor_comment_response.status_code == 201
    assert supervisor_comment_response.status_code == 201

    comments_response = client.get(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    history_response = client.get(
        f"/api/v1/tickets/{ticket['id']}/history",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    assert comments_response.status_code == history_response.status_code == 200

    with Session(engine) as session:
        identities = {
            user.id: (user.full_name, session.get(Role, user.role_id).name)
            for user in session.exec(select(User)).all()
        }
        client_id = ticket["client_id"]
        supervisor_id = session.exec(
            select(User.id)
            .join(Role, User.role_id == Role.id)
            .where(Role.name == "SUPERVISOR")
        ).one()

    expected_comment_roles = {
        client_comment["id"]: "CLIENTE",
        advisor_comment_response.json()["id"]: "ASESOR",
        supervisor_comment_response.json()["id"]: "SUPERVISOR",
    }
    for comment in comments_response.json():
        user_name, user_role = identities[comment["author_id"]]
        assert comment["author_name"] == user_name
        assert comment["author_role"] == user_role
        assert user_role == expected_comment_roles[comment["id"]]

    expected_history_roles = {
        "CREATED": (client_id, "CLIENTE"),
        "SLA_STARTED": (client_id, "CLIENTE"),
        "ASSIGNED": (supervisor_id, "SUPERVISOR"),
    }
    for item in history_response.json():
        if item["action"] in {"CREATED", "SLA_STARTED", "ASSIGNED"}:
            expected_actor_id, expected_role = expected_history_roles[item["action"]]
            assert item["actor_id"] == expected_actor_id
            assert item["actor_name"] == identities[expected_actor_id][0]
            assert item["actor_role"] == expected_role
        elif item["action"] == "COMMENT_ADDED":
            actor_name, actor_role = identities[item["actor_id"]]
            assert item["actor_name"] == actor_name
            assert item["actor_role"] == actor_role

    combined_response = comments_response.text + history_response.text
    assert "@demo.com" not in combined_response
    assert "password_hash" not in combined_response
    assert "access_token" not in combined_response


def test_ticket_participant_names_and_openapi_contract(ticket_client):
    client, engine = ticket_client
    ticket, _ = create_ticket(client, engine)
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    advisor_id = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {advisor_token}"}
    ).json()["id"]

    with Session(engine) as session:
        client_name = session.get(User, ticket["client_id"]).full_name
        advisor_name = session.get(User, advisor_id).full_name

    assert ticket["client_name"] == client_name
    assert ticket["assigned_advisor_name"] is None
    assert "email" not in ticket
    assert "password_hash" not in ticket
    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert assigned.status_code == 201
    assert assigned.json()["assigned_advisor_name"] == advisor_name

    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "client_name" in schema["TicketRead"]["properties"]
    assert "assigned_advisor_name" in schema["TicketRead"]["properties"]
    assert "author_name" in schema["CommentRead"]["properties"]
    assert "author_role" in schema["CommentRead"]["properties"]
    assert "actor_name" in schema["HistoryRead"]["properties"]
    assert "actor_role" in schema["HistoryRead"]["properties"]
    assert (
        "Nombre visible"
        in schema["TicketRead"]["properties"]["client_name"]["description"]
    )
    assert (
        "rol"
        in schema["CommentRead"]["properties"]["author_role"]["description"].casefold()
    )
    assert "actor" in schema["HistoryRead"]["properties"]["actor_name"]["description"]


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
