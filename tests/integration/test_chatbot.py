from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.faq import FAQ
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def chatbot_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(client: TestClient, email: str = "cliente@demo.com") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "demo-password-local"},
    )
    return response.json()["access_token"]


def test_creates_anonymous_conversation(chatbot_client):
    client, _ = chatbot_client

    response = client.post("/api/v1/chat/conversations")

    assert response.status_code == 201
    assert response.json()["user_id"] is None
    assert response.json()["status"] == "ACTIVE"


def test_creates_authenticated_conversation(chatbot_client):
    client, _ = chatbot_client
    token = login(client)

    response = client.post(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] is not None


def test_saves_user_and_bot_messages(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "¿Qué requisitos necesito para una tarjeta?"},
    )

    assert response.status_code == 200
    assert response.json()["user_message"]["content"].startswith("¿Qué requisitos")
    assert response.json()["bot_message"]["sender_type"] == "BOT"
    conversation = client.get(f"/api/v1/chat/conversations/{conversation_id}")
    assert len(conversation.json()["messages"]) == 2


def test_answers_active_faq_by_keyword_and_normalizes_accents(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "TARJÉTAS y REQUISITOS"},
    )

    assert response.status_code == 200
    assert response.json()["bot_message"]["intent"] == "TARJETAS_REQUISITOS"
    assert response.json()["resolved"] is True
    assert response.json()["offers_ticket"] is False


def test_ignores_inactive_faq_and_offers_ticket_without_creating_one(chatbot_client):
    client, engine = chatbot_client
    with Session(engine) as session:
        faq = session.exec(select(FAQ).where(FAQ.question.like("%requisitos%"))).first()
        faq.is_active = False
        session.commit()
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "requisitos tarjeta"},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is False
    assert response.json()["offers_ticket"] is True
    assert response.json()["bot_message"]["intent"] is None


def test_returns_unresolved_response_for_unknown_query(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "consulta completamente desconocida"},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is False
    assert response.json()["offers_ticket"] is True


def test_authorizes_conversation_owner_and_rejects_other_client(chatbot_client):
    client, _ = chatbot_client
    owner_token = login(client)
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Otro cliente",
            "email": "otro.cliente@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "otro.cliente@example.com",
            "password": "Demo-password-123",
        },
    )
    assert other_login.status_code == 200
    other_token = other_login.json()["access_token"]
    conversation = client.post(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()

    own = client.get(
        f"/api/v1/chat/conversations/{conversation['id']}",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    other = client.get(
        f"/api/v1/chat/conversations/{conversation['id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert own.status_code == 200
    assert other.status_code == 403


def test_links_anonymous_conversation_to_authenticated_client(chatbot_client):
    client, _ = chatbot_client
    conversation = client.post("/api/v1/chat/conversations").json()
    token = login(client)

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/link-user",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] is not None


@pytest.mark.parametrize("content", ["", "   ", "x" * 2001])
def test_rejects_empty_or_too_long_messages(chatbot_client, content: str):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )

    assert response.status_code == 422
