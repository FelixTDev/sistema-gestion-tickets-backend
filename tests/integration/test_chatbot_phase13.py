from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.chatbot.api.router import get_chatbot_service, get_ticket_service
from app.modules.chatbot.services import rag_service as rag_service_module
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.rag_service import RAGService, _RuntimeState
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.tickets.models.ticket import Ticket
from app.modules.tickets.services import ticket_service as ticket_service_module
from app.modules.tickets.services.ticket_service import TicketService
from app.modules.usuarios.api.auth_router import get_email_provider
from app.modules.usuarios.services.email_provider import NoopEmailProvider
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def phase13_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(
    client: TestClient,
    email: str = "cliente@demo.com",
    password: str = "demo-password-local",
) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_unhelpful_follow_up_keeps_context_and_asks_for_clarification(
    phase13_client,
) -> None:
    client, _ = phase13_client
    conversation = client.post("/api/v1/chat/conversations").json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"

    useful_answer = client.post(endpoint, json={"content": "tarjetas y requisitos"})
    assert useful_answer.status_code == 200
    assert useful_answer.json()["resolved"] is True

    follow_up = client.post(endpoint, json={"content": "eso no me ayudó"})

    assert follow_up.status_code == 200
    body = follow_up.json()
    assert body["requires_clarification"] is True
    assert body["suggested_action"] == "ASK_CLARIFICATION"
    assert body["faq_id"] == useful_answer.json()["faq_id"]
    assert body["conversation_status"] == "WAITING_CLARIFICATION"
    assert "qué parte" in body["bot_message"]["content"].casefold()
    assert body["offers_ticket"] is False


def test_anonymous_ticket_request_requires_login_then_structured_confirmation(
    phase13_client,
) -> None:
    client, engine = phase13_client
    conversation = client.post("/api/v1/chat/conversations").json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    helpful = client.post(endpoint, json={"content": "tarjetas y requisitos"})
    assert helpful.json()["resolved"] is True

    request = client.post(endpoint, json={"content": "mejor genera un ticket"})

    assert request.status_code == 200
    body = request.json()
    assert body["suggested_action"] == "AUTHENTICATION_REQUIRED"
    assert body["ticket_query"]["authentication_required"] is True
    assert body["ticket_draft"]["status"] == "INACTIVE"
    assert body["offers_ticket"] is False
    assert body["requires_clarification"] is False
    assert body["conversation_status"] == "ACTIVE"
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0

    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    linked = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/link-user",
        headers=headers,
    )
    assert linked.status_code == 200
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    payload = {
        "category_id": category.id,
        "subject": "Consulta de requisitos de tarjeta",
        "description": "La orientación recibida no resolvió mi consulta.",
        "priority": "MEDIA",
    }
    converted = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
        headers=headers,
        json=payload,
    )
    duplicate = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
        headers=headers,
        json=payload,
    )

    assert converted.status_code == 201, converted.text
    assert converted.json()["source"] == "CHATBOT"
    assert duplicate.status_code == 409


def test_natural_language_advisor_request_uses_explicit_escalation(
    phase13_client,
) -> None:
    client, engine = phase13_client
    conversation = client.post("/api/v1/chat/conversations").json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    client.post(endpoint, json={"content": "hola"})

    response = client.post(endpoint, json={"content": "quiero hablar con un asesor"})

    assert response.status_code == 200
    assert response.json()["suggested_action"] == "CONTACT_ADVISOR"
    assert response.json()["conversation_status"] == "ESCALATED"
    assert response.json()["offers_ticket"] is False
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


class ContextCapturingAIProvider:
    name = "test-context-provider"
    model = "test"

    def __init__(self) -> None:
        self.requests: list[AIProviderRequest] = []

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        self.requests.append(request)
        if not request.context:
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code="missing_test_context",
            )
        return AIGenerationResult.generated(
            text=request.context[0].content[:120],
            source_ids=[request.context[0].source_id],
            provider=self.name,
            model=self.model,
        )


def test_referential_follow_up_reaches_provider_with_bounded_recent_turns(
    phase13_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = phase13_client
    settings = rag_service_module.get_settings()
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )
    provider = ContextCapturingAIProvider()
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        rag_service=RAGService(provider=provider, runtime=_RuntimeState())
    )
    conversation = client.post("/api/v1/chat/conversations").json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    first = client.post(endpoint, json={"content": "TARJETAS y REQUISITOS"})
    assert first.status_code == 200
    assert first.json()["resolved"] is True

    follow_up = client.post(endpoint, json={"content": "y eso cómo se solicita?"})

    assert follow_up.status_code == 200
    assert len(provider.requests) == 2, follow_up.json()
    assert "Mensaje previo del cliente: TARJETAS y REQUISITOS" in (
        provider.requests[-1].user_message
    )
    assert "Respuesta previa del asistente:" in provider.requests[-1].user_message
    assert provider.requests[-1].context


def test_short_new_topic_does_not_inherit_unrelated_conversation_context(
    phase13_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = phase13_client
    settings = rag_service_module.get_settings()
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )
    provider = ContextCapturingAIProvider()
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        rag_service=RAGService(provider=provider, runtime=_RuntimeState())
    )
    conversation = client.post("/api/v1/chat/conversations").json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    client.post(endpoint, json={"content": "tarjetas y requisitos"})

    new_topic = client.post(endpoint, json={"content": "cuentas"})

    assert new_topic.status_code == 200
    assert provider.requests[-1].user_message == "cuentas"


def test_unverified_faqs_are_not_used_as_product_answers(phase13_client) -> None:
    client, engine = phase13_client
    with Session(engine) as session:
        for faq in session.exec(select(FAQ)).all():
            faq.source_url = None
            faq.source_verified_at = None
            session.add(faq)
        session.commit()
    conversation = client.post("/api/v1/chat/conversations").json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "tarjetas y requisitos"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is False
    assert body["sources"] == []
    assert body["offers_ticket"] is False


def test_conversion_authorization_and_daily_limit_remain_enforced(
    phase13_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = phase13_client
    settings = ticket_service_module.get_settings()
    monkeypatch.setattr(
        ticket_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_max_conversions_per_day": 1}),
    )
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[get_email_provider] = NoopEmailProvider
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Otro Cliente",
            "email": "otro-cliente@demo.com",
            "password": "Another-Strong-Password-42",
        },
    )
    assert registered.status_code == 201
    other_password = "Another-Strong-Password-42"
    other_token = login(client, "otro-cliente@demo.com", other_password)
    other_headers = {"Authorization": f"Bearer {other_token}"}
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    payload = {
        "category_id": category.id,
        "subject": "Consulta del chatbot",
        "description": "Solicito atención para revisar esta consulta.",
    }
    conversation_ids: list[str] = []
    for _ in range(2):
        conversation = client.post("/api/v1/chat/conversations").json()
        conversation_ids.append(conversation["id"])
        client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/messages",
            json={"content": "hola"},
        )
        linked = client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/link-user",
            headers=headers,
        )
        assert linked.status_code == 200

    unauthorized = client.post(
        f"/api/v1/chat/conversations/{conversation_ids[0]}/convert-to-ticket",
        headers=other_headers,
        json=payload,
    )
    first = client.post(
        f"/api/v1/chat/conversations/{conversation_ids[0]}/convert-to-ticket",
        headers=headers,
        json=payload,
    )
    limited = client.post(
        f"/api/v1/chat/conversations/{conversation_ids[1]}/convert-to-ticket",
        headers=headers,
        json=payload,
    )

    assert unauthorized.status_code == 403
    assert first.status_code == 201, first.text
    assert limited.status_code == 429


class FailingConversionAuditService(AuditService):
    def record(self, session: Session, **kwargs):
        if (
            kwargs.get("event_type") == "CHATBOT"
            and kwargs.get("action") == "CONVERTED_TO_TICKET"
        ):
            raise RuntimeError("simulated audit persistence failure")
        return super().record(session, **kwargs)


def test_failed_conversion_audit_rolls_back_ticket_and_conversation(
    phase13_client,
) -> None:
    client, engine = phase13_client
    conversation = client.post("/api/v1/chat/conversations").json()
    client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "hola"},
    )
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/link-user",
        headers=headers,
    )
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    draft = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        headers=headers,
        json={
            "content": (
                f"Quiero crear ticket. Categoria: {category.name}. "
                "Asunto: Consulta para validar atomicidad. "
                "Descripcion: La conversión debe revertirse si falla la auditoría."
            )
        },
    )
    assert draft.status_code == 200
    assert draft.json()["ticket_draft"]["status"] == "READY_FOR_CONFIRMATION"
    app.dependency_overrides[get_ticket_service] = lambda: TicketService(
        audit_service=FailingConversionAuditService()
    )

    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
            headers=headers,
            json={
                "category_id": category.id,
                "subject": "Consulta para validar atomicidad",
                "description": "La conversión debe revertirse si falla la auditoría.",
                "priority": "MEDIA",
            },
        )

    assert response.status_code == 500
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0
        saved = session.get(models.Conversation, conversation["id"])
        assert saved is not None
        assert saved.status.value == "ACTIVE"
        assert saved.context_json["ticket_creation_draft"]["status"] == (
            "READY_FOR_CONFIRMATION"
        )


class FailingAIProvider:
    name = "test-failing-provider"
    model = "test"

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        raise RuntimeError("provider unavailable")


class UnavailableFAQRepository(FAQRepository):
    def list_active(self, session: Session):
        raise RuntimeError("knowledge retrieval unavailable")


def test_provider_failure_returns_safe_grounded_fallback_without_ticket(
    phase13_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = phase13_client
    settings = rag_service_module.get_settings()
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        rag_service=RAGService(provider=FailingAIProvider(), runtime=_RuntimeState())
    )
    conversation = client.post("/api/v1/chat/conversations").json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "tarjetas y requisitos"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["response_source"] == "DETERMINISTIC"
    assert body["suggested_action"] == "NONE"
    assert body["conversation_status"] == "ACTIVE"
    assert body["offers_ticket"] is False
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_retrieval_failure_returns_controlled_non_escalating_fallback(
    phase13_client,
) -> None:
    client, engine = phase13_client
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        faq_repository=UnavailableFAQRepository()
    )
    conversation = client.post("/api/v1/chat/conversations").json()

    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post(
            f"/api/v1/chat/conversations/{conversation['id']}/messages",
            json={"content": "tarjetas y requisitos"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["fallback_reason"] == "KNOWLEDGE_UNAVAILABLE"
    assert body["suggested_action"] == "NONE"
    assert body["offers_ticket"] is False
    assert body["conversation_status"] == "ACTIVE"
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_reset_preserves_history_and_frontend_can_start_a_new_conversation(
    phase13_client,
) -> None:
    client, _ = phase13_client
    first = client.post("/api/v1/chat/conversations").json()
    client.post(
        f"/api/v1/chat/conversations/{first['id']}/messages",
        json={"content": "hola"},
    )

    reset = client.post(f"/api/v1/chat/conversations/{first['id']}/reset")
    second = client.post("/api/v1/chat/conversations")

    assert reset.status_code == 200
    assert reset.json()["turn_count"] == 0
    assert len(reset.json()["messages"]) == 2
    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


def test_openapi_exposes_typed_actions_and_existing_conversion_contract() -> None:
    schema = app.openapi()
    components = schema["components"]["schemas"]
    response_fields = components["BotMessageResponse"]["properties"]
    conversion_path = "/api/v1/chat/conversations/{conversation_id}/convert-to-ticket"

    assert response_fields["suggested_action"]["$ref"].endswith(
        "/ChatbotSuggestedAction"
    )
    assert components["ChatbotSuggestedAction"]["enum"] == [
        "NONE",
        "ASK_CLARIFICATION",
        "CONVERT_TO_TICKET",
        "CONTACT_ADVISOR",
        "AUTHENTICATION_REQUIRED",
        "LIST_MY_TICKETS",
        "COUNT_MY_TICKETS",
        "TICKET_STATUS",
        "ADVISOR_RESPONSE_STATUS",
        "LATEST_ADVISOR_RESPONSE",
        "CLIENT_ACTION_REQUIRED",
        "CLARIFY_TICKET_CODE",
    ]
    assert conversion_path in schema["paths"]
    assert (
        "confianza de la última respuesta"
        in schema["paths"][conversion_path]["post"]["description"]
    )
    assert (
        "TicketCreate"
        in schema["paths"][conversion_path]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
    )
