from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.auditoria.models.audit_log import AuditLog
from app.modules.chatbot.models.conversation import ConversationStatus
from app.modules.chatbot.services import chatbot_service as chatbot_service_module
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.tickets.services import ticket_service as ticket_service_module
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def phase11_client() -> Generator[tuple[TestClient, object], None, None]:
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
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_high_confidence_includes_source_and_context(phase11_client) -> None:
    client, _ = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "TARJÉTAS y REQUISITOS"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert body["confidence"] >= 0.7
    assert body["faq_id"]
    assert body["source_category"]
    assert body["requires_clarification"] is False
    assert body["conversation_status"] == "ACTIVE"

    read = client.get(f"/api/v1/chat/conversations/{conversation['id']}")
    assert read.status_code == 200
    assert read.json()["turn_count"] == 1
    assert read.json()["last_faq_id"] == body["faq_id"]


def test_low_confidence_is_safe_and_offers_escalation(phase11_client) -> None:
    client, _ = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "Necesito algo que no existe en el catálogo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is False
    assert body["confidence"] < 0.35
    assert body["offers_ticket"] is True
    assert body["fallback_reason"]
    assert body["conversation_status"] == "ESCALATED"
    assert "no encontré" in body["bot_message"]["content"].casefold()


def test_manual_escalation_and_reset_are_idempotent(phase11_client) -> None:
    client, _ = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    escalation = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/escalate",
        json={"reason": "Necesito atención humana"},
    )
    assert escalation.status_code == 200
    assert escalation.json()["status"] == ConversationStatus.ESCALATED

    reset = client.post(f"/api/v1/chat/conversations/{conversation['id']}/reset")
    assert reset.status_code == 200
    assert reset.json()["status"] == ConversationStatus.ACTIVE
    assert reset.json()["turn_count"] == 0


def test_authenticated_client_can_link_and_convert_once(phase11_client) -> None:
    client, engine = phase11_client
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    conversation = client.post("/api/v1/chat/conversations").json()
    client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "consulta fuera del alcance"},
    )
    token = login(client, "cliente@demo.com")
    linked = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/link-user",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert linked.status_code == 200
    payload = {
        "category_id": category.id,
        "subject": "Seguimiento de consulta chatbot",
        "description": "Consulta derivada desde una conversación real",
    }
    converted = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert converted.status_code == 201, converted.text
    assert converted.json()["source"] == "CHATBOT"
    duplicate = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert duplicate.status_code == 409

    read = client.get(
        f"/api/v1/chat/conversations/{conversation['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read.json()["status"] == "CONVERTED_TO_TICKET"


@pytest.mark.parametrize("email", ["asesor@demo.com", "supervisor@demo.com"])
def test_internal_roles_cannot_activate_chatbot(phase11_client, email: str) -> None:
    client, _ = phase11_client
    token = login(client, email)
    response = client.post(
        "/api/v1/chat/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_feedback_is_persisted_without_unnecessary_personal_data(
    phase11_client,
) -> None:
    client, engine = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    message = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "requisitos de tarjeta"},
    )
    assert message.status_code == 200
    feedback = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/feedback",
        json={
            "is_helpful": False,
            "escalation_accepted": True,
            "reason": "No resolvió mi duda",
        },
    )
    assert feedback.status_code == 201
    with Session(engine) as session:
        row = session.exec(select(FAQFeedback)).one()
        assert row.comment == "No resolvió mi duda"
        assert row.escalation_accepted is True
        assert row.user_id is None


def test_expired_and_inactive_faqs_are_never_used(phase11_client) -> None:
    client, engine = phase11_client
    with Session(engine) as session:
        faq = session.exec(select(FAQ).where(FAQ.is_active)).first()
        question = faq.question
        faq.published_at = datetime.now(UTC) + timedelta(days=1)
        session.add(faq)
        session.commit()
    conversation = client.post("/api/v1/chat/conversations").json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": question},
    )
    assert response.status_code == 200
    assert response.json()["resolved"] is False


def test_malicious_markup_and_internal_audit_are_safe(phase11_client) -> None:
    client, engine = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "<script>alert('x')</script>"},
    )
    assert response.status_code == 422
    with Session(engine) as session:
        rows = session.exec(select(AuditLog)).all()
        assert all(
            "authorization" not in str(row.metadata_json).casefold() for row in rows
        )


def test_message_length_uses_configured_limit(phase11_client) -> None:
    client, _ = phase11_client
    conversation = client.post("/api/v1/chat/conversations").json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "x" * 2001},
    )
    assert response.status_code == 422


def test_anonymous_conversation_limit_is_scoped_and_safe(
    phase11_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = phase11_client
    original_settings = chatbot_service_module.get_settings()
    monkeypatch.setattr(
        chatbot_service_module,
        "get_settings",
        lambda: original_settings.model_copy(
            update={"chatbot_max_anonymous_conversations_per_hour": 1}
        ),
    )
    assert client.post("/api/v1/chat/conversations").status_code == 201
    limited = client.post("/api/v1/chat/conversations")
    assert limited.status_code == 429


def test_daily_chatbot_conversion_limit_is_enforced(
    phase11_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = phase11_client
    original_settings = ticket_service_module.get_settings()
    monkeypatch.setattr(
        ticket_service_module,
        "get_settings",
        lambda: original_settings.model_copy(
            update={"chatbot_max_conversions_per_day": 1}
        ),
    )
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    token = login(client, "cliente@demo.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "category_id": category.id,
        "subject": "Conversión desde chatbot",
        "description": "Consulta real derivada a atención humana",
    }
    for _ in range(2):
        conversation = client.post("/api/v1/chat/conversations").json()
        client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/messages",
            json={"content": "consulta que no existe"},
        )
        client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/link-user",
            headers=headers,
        )
        result = client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
            headers=headers,
            json=payload,
        )
        if _ == 0:
            assert result.status_code == 201, result.text
        else:
            assert result.status_code == 429
