from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.chatbot.api.router import get_chatbot_service
from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.services import rag_service as rag_service_module
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket
from app.modules.usuarios.api.auth_router import get_email_provider
from app.modules.usuarios.services.email_provider import NoopEmailProvider
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def phase3_client() -> Generator[tuple[TestClient, object], None, None]:
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


def create_linked_conversation(client: TestClient) -> tuple[str, dict[str, str]]:
    headers = {"Authorization": f"Bearer {login(client)}"}
    conversation = client.post("/api/v1/chat/conversations", headers=headers).json()
    return conversation["id"], headers


def send(client: TestClient, conversation_id: str, content: str, headers=None):
    return client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=headers,
    )


def test_anonymous_ticket_request_requires_login_without_creating_ticket(
    phase3_client,
) -> None:
    client, engine = phase3_client
    conversation = client.post("/api/v1/chat/conversations").json()

    response = send(client, conversation["id"], "Quiero crear un ticket")

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_action"] == "AUTHENTICATION_REQUIRED"
    assert body["ticket_query"]["authentication_actions"] == ["LOGIN", "REGISTER"]
    assert body["ticket_draft"]["status"] == "INACTIVE"
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_anonymous_conversation_can_be_linked_before_authenticated_draft(
    phase3_client,
) -> None:
    client, _ = phase3_client
    conversation = client.post("/api/v1/chat/conversations").json()
    conversation_id = conversation["id"]
    send(client, conversation_id, "Quiero crear una solicitud")
    headers = {"Authorization": f"Bearer {login(client)}"}

    linked = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/link-user", headers=headers
    )
    resumed = send(client, conversation_id, "Continúa con el borrador", headers)
    response = send(
        client,
        conversation_id,
        "Categoría: TARJETAS. Asunto: Reposición de tarjeta",
        headers,
    )

    assert linked.status_code == 200
    assert resumed.json()["ticket_draft"]["status"] == "COLLECTING"
    assert response.status_code == 200
    assert response.json()["ticket_draft"]["status"] == "COLLECTING"
    saved = client.get(f"/api/v1/chat/conversations/{conversation_id}", headers=headers)
    assert saved.json()["user_id"] == linked.json()["user_id"]
    assert saved.json()["ticket_draft"]["fields"]["category_id"]


def test_progressive_draft_is_typed_persisted_and_only_conversion_creates_ticket(
    phase3_client,
) -> None:
    client, engine = phase3_client
    conversation_id, headers = create_linked_conversation(client)
    with Session(engine) as session:
        category = session.exec(
            select(TicketCategory).where(TicketCategory.name == "TARJETAS")
        ).one()

    response = send(
        client,
        conversation_id,
        "Quiero crear un ticket. Categoría: TARJETAS. "
        "Asunto: Reposición de tarjeta. "
        "Descripción: No recibí la tarjeta solicitada. Prioridad: ALTA.",
        headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ticket_draft"]["status"] == "READY_FOR_CONFIRMATION"
    assert body["ticket_draft"]["fields"] == {
        "category_id": category.id,
        "subject": "Reposición de tarjeta",
        "description": "No recibí la tarjeta solicitada.",
        "priority": "ALTA",
    }
    assert body["ticket_draft"]["missing_fields"] == []
    assert body["suggested_action"] == "CONVERT_TO_TICKET"
    ambiguous_confirmation = send(client, conversation_id, "sí", headers)
    assert ambiguous_confirmation.json()["ticket_draft"]["status"] == (
        "READY_FOR_CONFIRMATION"
    )
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0

    payload = {
        "category_id": category.id,
        "subject": "Reposición de tarjeta",
        "description": "No recibí la tarjeta solicitada.",
        "priority": "ALTA",
    }
    mismatched = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
        headers=headers,
        json={**payload, "subject": "Otro asunto"},
    )
    assert mismatched.status_code == 409
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0

    converted = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
        headers=headers,
        json=payload,
    )
    duplicate = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
        headers=headers,
        json=payload,
    )

    assert converted.status_code == 201, converted.text
    assert converted.json()["conversation_id"] == conversation_id
    assert converted.json()["source"] == "CHATBOT"
    assert duplicate.status_code == 409
    with Session(engine) as session:
        ticket = session.exec(select(Ticket)).one()
        history = session.exec(
            select(TicketHistory).where(TicketHistory.ticket_id == ticket.id)
        ).all()
        saved = session.get(Conversation, conversation_id)
        assert sum(item.action == "CREATED" for item in history) == 1
        assert saved.status.value == "CONVERTED_TO_TICKET"
        assert saved.user_id == converted.json()["client_id"]


def test_incomplete_draft_rejects_conversion_even_with_a_complete_request_body(
    phase3_client,
) -> None:
    client, engine = phase3_client
    conversation_id, headers = create_linked_conversation(client)
    start = send(client, conversation_id, "Quiero crear un ticket", headers)
    assert start.json()["ticket_draft"]["status"] == "COLLECTING"
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
        headers=headers,
        json={
            "category_id": category.id,
            "subject": "Consulta incompleta",
            "description": "Intento de conversión sin completar el borrador.",
        },
    )

    assert response.status_code == 409
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_ambiguous_values_are_not_guessed_and_active_options_are_typed(
    phase3_client,
) -> None:
    client, engine = phase3_client
    conversation_id, headers = create_linked_conversation(client)

    response = send(
        client,
        conversation_id,
        "Quiero ticket de TARJETAS y CUENTAS, prioridad ALTA o URGENTE",
        headers,
    )

    draft = response.json()["ticket_draft"]
    assert draft["status"] == "COLLECTING"
    assert draft["fields"]["category_id"] is None
    assert draft["fields"]["priority"] is None
    assert "CATEGORY_ID" in [item["field"] for item in draft["validation_errors"]]
    assert "PRIORITY" in [item["field"] for item in draft["validation_errors"]]
    assert {item["name"] for item in draft["category_options"]} == {
        "TARJETAS",
        "CUENTAS",
        "CREDITOS",
        "BANCA_DIGITAL",
        "RECLAMOS_SIMPLES",
        "SOLICITUDES_INFORMACION",
    }
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_start_message_reuses_incident_description_and_contract_default_priority(
    phase3_client,
) -> None:
    client, engine = phase3_client
    conversation_id, headers = create_linked_conversation(client)

    response = send(
        client,
        conversation_id,
        "Quiero crear un ticket por no recibí la tarjeta solicitada",
        headers,
    )

    draft = response.json()["ticket_draft"]
    with Session(engine) as session:
        category = session.exec(
            select(TicketCategory).where(TicketCategory.name == "TARJETAS")
        ).one()
    assert draft["status"] == "COLLECTING"
    assert draft["fields"]["category_id"] == category.id
    assert draft["fields"]["subject"] is None
    assert draft["fields"]["description"] == "no recibí la tarjeta solicitada"
    assert draft["fields"]["priority"] == "MEDIA"
    assert draft["missing_fields"] == ["SUBJECT"]


def test_correction_resume_cancel_and_unrelated_question_preserve_draft(
    phase3_client,
) -> None:
    client, _ = phase3_client
    conversation_id, headers = create_linked_conversation(client)
    first = send(
        client,
        conversation_id,
        "Quiero crear ticket. Categoría: TARJETAS. Asunto: Entrega pendiente.",
        headers,
    )
    before = first.json()["ticket_draft"]["fields"]
    greeting = send(client, conversation_id, "hola", headers)
    assert greeting.json()["ticket_draft"]["fields"] == before
    unrelated = send(client, conversation_id, "¿Cómo ingreso a banca digital?", headers)
    assert unrelated.status_code == 200, unrelated.text
    assert unrelated.json()["ticket_draft"]["fields"] == before

    resumed = send(client, conversation_id, "Continúa con el borrador", headers)
    assert resumed.json()["ticket_draft"]["fields"] == before
    corrected_category = send(client, conversation_id, "Categoría: CUENTAS", headers)
    assert (
        corrected_category.json()["ticket_draft"]["fields"]["category_id"]
        != (before["category_id"])
    )
    corrected = send(
        client, conversation_id, "Corrige asunto: Tarjeta no recibida", headers
    )
    assert corrected.json()["ticket_draft"]["fields"]["subject"] == (
        "Tarjeta no recibida"
    )
    corrected_description = send(
        client,
        conversation_id,
        "Descripción: No aparece el movimiento en la cuenta.",
        headers,
    )
    assert corrected_description.json()["ticket_draft"]["fields"]["description"] == (
        "No aparece el movimiento en la cuenta."
    )
    corrected_priority = send(client, conversation_id, "Prioridad: ALTA", headers)
    assert corrected_priority.json()["ticket_draft"]["fields"]["priority"] == "ALTA"
    saved = client.get(
        f"/api/v1/chat/conversations/{conversation_id}", headers=headers
    ).json()
    assert saved["ticket_draft"]["fields"]["subject"] == "Tarjeta no recibida"

    cancelled = send(client, conversation_id, "Cancelo el borrador", headers)
    assert cancelled.json()["ticket_draft"]["status"] == "CANCELLED"
    assert all(
        value is None for value in cancelled.json()["ticket_draft"]["fields"].values()
    )
    restarted = send(client, conversation_id, "Quiero crear otro ticket", headers)
    assert restarted.json()["ticket_draft"]["status"] == "COLLECTING"
    restarted_fields = restarted.json()["ticket_draft"]["fields"]
    assert restarted_fields["category_id"] is None
    assert restarted_fields["subject"] is None
    assert restarted_fields["description"] is None
    assert restarted_fields["priority"] == "MEDIA"


def test_ticket_draft_never_reaches_external_ai_provider(
    phase3_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = phase3_client
    settings = rag_service_module.get_settings()
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )

    class CapturingProvider:
        name = "capturing-provider"
        model = "test"

        def __init__(self) -> None:
            self.requests: list[AIProviderRequest] = []

        def generate(self, request: AIProviderRequest) -> AIGenerationResult:
            self.requests.append(request)
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code="unexpected_provider_call",
            )

    provider = CapturingProvider()
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        ai_provider=provider
    )
    conversation_id, headers = create_linked_conversation(client)
    send(client, conversation_id, "Quiero crear un ticket", headers)
    send(
        client,
        conversation_id,
        "No puedo ingresar a la aplicación; referencia TCK-PRIVATE-001",
        headers,
    )

    assert provider.requests == []


def test_openapi_exposes_ticket_draft_status_fields_and_real_enums() -> None:
    components = app.openapi()["components"]["schemas"]
    bot_fields = components["BotMessageResponse"]["properties"]
    conversation_fields = components["ConversationRead"]["properties"]

    assert "ticket_draft" in bot_fields
    assert "ticket_draft" in conversation_fields
    assert components["ChatbotTicketDraftStatus"]["enum"] == [
        "INACTIVE",
        "COLLECTING",
        "READY_FOR_CONFIRMATION",
        "CANCELLED",
    ]
    assert components["TicketPriority"]["enum"] == [
        "BAJA",
        "MEDIA",
        "ALTA",
        "URGENTE",
    ]
    assert set(components["TicketCreate"]["required"]) == {
        "category_id",
        "subject",
        "description",
    }
    assert components["ChatbotTicketDraftField"]["enum"] == [
        "CATEGORY_ID",
        "SUBJECT",
        "DESCRIPTION",
        "PRIORITY",
    ]


def test_other_client_cannot_read_change_or_convert_a_private_draft(
    phase3_client,
) -> None:
    client, engine = phase3_client
    app.dependency_overrides[get_email_provider] = NoopEmailProvider
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Otro Cliente",
            "email": "otro-cliente-phase3@demo.com",
            "password": "Another-Strong-Password-42",
        },
    )
    assert registered.status_code == 201, registered.text
    conversation = client.post("/api/v1/chat/conversations").json()
    conversation_id = conversation["id"]
    send(client, conversation_id, "Quiero crear un ticket")
    owner_headers = {"Authorization": f"Bearer {login(client)}"}
    link = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/link-user",
        headers=owner_headers,
    )
    assert link.status_code == 200
    draft = send(
        client,
        conversation_id,
        "Categoría: TARJETAS. Asunto: Datos privados. "
        "Descripción: Información privada de mi solicitud.",
        owner_headers,
    )
    assert draft.json()["ticket_draft"]["status"] == "READY_FOR_CONFIRMATION"

    other_token = login(
        client,
        "otro-cliente-phase3@demo.com",
        "Another-Strong-Password-42",
    )
    other_headers = {"Authorization": f"Bearer {other_token}"}
    inaccessible = client.get(
        f"/api/v1/chat/conversations/{conversation_id}", headers=other_headers
    )
    unmodifiable = send(client, conversation_id, "Categoría: CUENTAS", other_headers)
    unconvertible = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
        headers=other_headers,
        json=draft.json()["ticket_draft"]["fields"],
    )

    assert inaccessible.status_code == 403
    assert unmodifiable.status_code == 403
    assert unconvertible.status_code == 403
    assert "Información privada" not in inaccessible.text
    assert "Información privada" not in unmodifiable.text
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0
