from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.faq import FAQ
from app.modules.tickets.models.ticket import Ticket
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


@pytest.mark.parametrize(
    "content",
    ["hola", "HOLA!!!", " ¡Buenas! ", "Buenos   días.", "¿BUENAS tardes?"],
)
def test_greeting_gets_a_friendly_reply_without_escalating(chatbot_client, content):
    client, engine = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bot_message"]["content"] == (
        "¡Hola! ¿En qué puedo ayudarte hoy? Puedo orientarte sobre preguntas "
        "frecuentes o el seguimiento de una solicitud."
    )
    assert body["resolved"] is True
    assert body["offers_ticket"] is False
    assert body["requires_clarification"] is False
    assert body["fallback_reason"] is None
    assert body["conversation_status"] == "ACTIVE"
    assert body["response_source"] == "DETERMINISTIC"
    assert body["faq_id"] is None
    assert body["sources"] == []

    conversation = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()
    assert conversation["status"] == "ACTIVE"
    assert conversation["escalation_reason"] is None
    assert conversation["escalated_at"] is None
    assert len(conversation["messages"]) == 2
    with Session(engine) as session:
        assert session.exec(select(Ticket)).all() == []

    advisor_token = login(client, "asesor@demo.com")
    denied = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "hola"},
    )
    assert denied.status_code == 403


def test_greeting_with_a_real_question_uses_the_normal_query_flow(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "Hola, no puedo ingresar a mi cuenta"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bot_message"]["content"] != (
        "¡Hola! ¿En qué puedo ayudarte hoy? Puedo orientarte sobre preguntas "
        "frecuentes o el seguimiento de una solicitud."
    )
    assert body["requires_clarification"] is True
    assert body["conversation_status"] == "WAITING_CLARIFICATION"
    assert body["offers_ticket"] is False
    assert body["response_source"] == "DETERMINISTIC"


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


@pytest.mark.parametrize(
    "content",
    [
        "Preguntas frecuentes",
        "¿Qué preguntas me puedes dar?",
        "¿Qué temas puedes responder?",
    ],
)
def test_faq_discovery_lists_published_questions_and_selected_question_is_answered(
    chatbot_client, content: str
):
    client, engine = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    discovery = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )

    assert discovery.status_code == 200
    body = discovery.json()
    options = body["clarification_options"]
    assert body["bot_message"]["intent"] == "FAQ_DISCOVERY"
    assert body["resolved"] is False
    assert body["requires_clarification"] is True
    assert body["offers_ticket"] is False
    assert body["fallback_reason"] is None
    assert body["conversation_status"] == "WAITING_CLARIFICATION"
    assert body["response_source"] == "DETERMINISTIC"
    assert 1 <= len(options) <= 4

    with Session(engine) as session:
        public_questions = {
            faq.title or faq.question
            for faq in session.exec(
                select(FAQ).where(
                    FAQ.status == FAQ.Status.PUBLISHED,
                    FAQ.is_active,
                )
            ).all()
        }
        assert set(options) <= public_questions
    assert {source["title"] for source in body["sources"]} == set(options)

    answer = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": options[0]},
    )

    assert answer.status_code == 200
    answer_body = answer.json()
    assert answer_body["resolved"] is True
    assert answer_body["requires_clarification"] is False
    assert answer_body["offers_ticket"] is False
    assert answer_body["faq_id"] is not None
    assert answer_body["conversation_status"] == "ACTIVE"
    assert answer_body["response_source"] == "DETERMINISTIC"
    assert answer_body["bot_message"]["content"] != (
        "Para orientarte mejor, ¿puedes contarme un poco más sobre "
        "lo que necesitas o el inconveniente que tienes?"
    )
    with Session(engine) as session:
        assert session.exec(select(Ticket)).all() == []


def test_ignores_inactive_faq_and_asks_for_clarification_without_ticket(
    chatbot_client,
):
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
    assert response.json()["requires_clarification"] is True
    assert response.json()["offers_ticket"] is False
    assert response.json()["conversation_status"] == "WAITING_CLARIFICATION"
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
    assert response.json()["requires_clarification"] is True
    assert response.json()["offers_ticket"] is False
    assert response.json()["fallback_reason"] is None
    assert response.json()["conversation_status"] == "WAITING_CLARIFICATION"
    assert response.json()["response_source"] == "DETERMINISTIC"


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
