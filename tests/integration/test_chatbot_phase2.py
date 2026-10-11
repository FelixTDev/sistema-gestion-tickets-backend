from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.chatbot.api.router import get_chatbot_service
from app.modules.chatbot.services import rag_service as rag_service_module
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.rag_service import RAGService, _RuntimeState
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import (
    Ticket,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.modules.tickets.services.ticket_service import TicketService
from app.modules.usuarios.models.role import Role
from app.modules.usuarios.models.user import User
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def phase2_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@demo.com", "password": "demo-password-local"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def ask(client: TestClient, content: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    conversation = client.post("/api/v1/chat/conversations", headers=headers).json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        headers=headers,
        json={"content": content},
    )
    assert response.status_code == 200, response.text
    return response.json()


def add_other_client(session: Session) -> User:
    client_role = session.exec(select(Role).where(Role.name == "CLIENTE")).one()
    other = User(
        full_name="Otra Cliente",
        email="otra-cliente@example.test",
        password_hash="not-used-by-this-test",
        role_id=client_role.id,
        email_verified=True,
    )
    session.add(other)
    session.flush()
    return other


def add_ticket(
    session: Session,
    *,
    client_id: str,
    tracking_code: str,
    status: TicketStatus = TicketStatus.NUEVO,
    created_at: datetime | None = None,
    comments: tuple[tuple[str, str, datetime], ...] = (),
) -> Ticket:
    client = session.get(User, client_id)
    assert client is not None
    advisor = session.exec(select(User).where(User.email == "asesor@demo.com")).one()
    category = session.exec(
        select(TicketCategory).where(TicketCategory.name == "TARJETAS")
    ).one()
    created = created_at or datetime(2026, 10, 10, 12, tzinfo=UTC)
    assigned_advisor_id = (
        advisor.id
        if status
        in {
            TicketStatus.ASIGNADO,
            TicketStatus.EN_PROCESO,
            TicketStatus.PENDIENTE_CLIENTE,
            TicketStatus.RESUELTO,
        }
        else None
    )
    ticket = Ticket(
        tracking_code=tracking_code,
        client_id=client.id,
        category_id=category.id,
        subject=f"Asunto {tracking_code}",
        description="Descripción de prueba del ticket.",
        priority=TicketPriority.MEDIA,
        status=status,
        source=TicketSource.MANUAL,
        assigned_advisor_id=assigned_advisor_id,
        created_at=created,
        updated_at=created + timedelta(minutes=5),
    )
    session.add(ticket)
    session.flush()
    session.add(
        TicketHistory(
            ticket_id=ticket.id,
            actor_id=client.id,
            action="CREATED",
            description="Ticket creado para la prueba.",
            created_at=created,
        )
    )
    for author_email, content, comment_at in comments:
        author = session.exec(select(User).where(User.email == author_email)).one()
        session.add(
            TicketComment(
                ticket_id=ticket.id,
                author_id=author.id,
                content=content,
                created_at=comment_at,
            )
        )
    return ticket


def ticket_snapshot(engine: object) -> tuple:
    with Session(engine) as session:
        tickets = session.exec(select(Ticket)).all()
        comments = session.exec(select(TicketComment)).all()
        history = session.exec(select(TicketHistory)).all()
        return (
            tuple(
                sorted(
                    (
                        ticket.id,
                        ticket.tracking_code,
                        ticket.client_id,
                        ticket.status.value,
                        ticket.version,
                        ticket.updated_at,
                        ticket.assigned_advisor_id,
                    )
                    for ticket in tickets
                )
            ),
            tuple(
                sorted(
                    (comment.id, comment.ticket_id, comment.author_id, comment.content)
                    for comment in comments
                )
            ),
            tuple(
                sorted(
                    (entry.id, entry.ticket_id, entry.action, entry.description)
                    for entry in history
                )
            ),
        )


def test_anonymous_ticket_query_returns_login_action_without_ticket_data(
    phase2_client,
) -> None:
    client, engine = phase2_client
    with Session(engine) as session:
        own = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        add_ticket(
            session,
            client_id=own.id,
            tracking_code="TCK-PRIVATE-100",
        )
        session.commit()
    before = ticket_snapshot(engine)

    response = ask(client, "¿Cuántos tickets tengo?")

    assert response["suggested_action"] == "AUTHENTICATION_REQUIRED"
    assert response["ticket_query"]["authentication_required"] is True
    assert response["ticket_query"]["authentication_actions"] == ["LOGIN", "REGISTER"]
    assert response["ticket_query"]["tickets"] == []
    assert response["ticket_query"]["total"] is None
    assert "TCK-PRIVATE-100" not in str(response)
    assert ticket_snapshot(engine) == before


def test_owned_code_is_visible_but_other_and_missing_codes_are_indistinguishable(
    phase2_client,
) -> None:
    client, engine = phase2_client
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        other = add_other_client(session)
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-OWNER-101",
            status=TicketStatus.EN_PROCESO,
        )
        add_ticket(
            session,
            client_id=other.id,
            tracking_code="TCK-OTHER-202",
            status=TicketStatus.EN_PROCESO,
        )
        session.commit()
    token = login(client)
    before = ticket_snapshot(engine)

    own = ask(client, "¿En qué estado está el ticket TCK-OWNER-101?", token)
    other_code = ask(client, "¿En qué estado está el ticket TCK-OTHER-202?", token)
    missing = ask(client, "¿En qué estado está el ticket TCK-ABSENT-303?", token)

    assert own["suggested_action"] == "TICKET_STATUS"
    assert own["ticket_query"]["ticket"]["tracking_code"] == "TCK-OWNER-101"
    assert own["ticket_query"]["ticket"]["status"] == "EN_PROCESO"
    assert own["ticket_query"]["ticket"]["status_label"] == "En proceso"
    assert own["ticket_query"]["ticket"]["priority_label"] == "Media"
    assert other_code["bot_message"]["content"] == missing["bot_message"]["content"]
    assert other_code["ticket_query"]["found"] is False
    assert other_code["ticket_query"]["ticket"] is None
    assert "TCK-OTHER-202" not in other_code["bot_message"]["content"]
    assert "TCK-OTHER-202" not in str(other_code["ticket_query"])
    assert ticket_snapshot(engine) == before


def test_empty_list_and_count_use_real_totals(phase2_client) -> None:
    client, engine = phase2_client
    token = login(client)

    listed = ask(client, "Muéstrame mis tickets recientes.", token)
    counted = ask(client, "¿Cuántos tickets tengo?", token)

    assert listed["suggested_action"] == "LIST_MY_TICKETS"
    assert listed["ticket_query"]["tickets"] == []
    assert listed["ticket_query"]["total"] == 0
    assert listed["ticket_query"]["pagination"] == {
        "page": 1,
        "page_size": 5,
        "total_pages": 0,
    }
    assert counted["suggested_action"] == "COUNT_MY_TICKETS"
    assert counted["ticket_query"]["total"] == 0
    with Session(engine) as session:
        assert session.exec(select(func.count(Ticket.id))).one() == 0


def test_list_pagination_and_count_are_owner_scoped_and_open_states_use_enums(
    phase2_client,
) -> None:
    client, engine = phase2_client
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        other = add_other_client(session)
        statuses = [
            TicketStatus.NUEVO,
            TicketStatus.EN_PROCESO,
            TicketStatus.PENDIENTE_CLIENTE,
            TicketStatus.RESUELTO,
            TicketStatus.ASIGNADO,
            TicketStatus.CERRADO,
            TicketStatus.CANCELADO,
        ]
        for index, ticket_status in enumerate(statuses):
            add_ticket(
                session,
                client_id=owner.id,
                tracking_code=f"TCK-OWN-{index:03}",
                status=ticket_status,
                created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=index),
            )
        add_ticket(
            session,
            client_id=other.id,
            tracking_code="TCK-OTHER-999",
        )
        session.commit()
    token = login(client)
    before = ticket_snapshot(engine)

    first = ask(client, "Muéstrame mis tickets recientes.", token)
    second = ask(client, "Muéstrame mis tickets recientes página 2.", token)
    counted = ask(client, "¿Cuántos tickets tengo?", token)
    open_count = ask(client, "¿Cuántos tickets abiertos tengo?", token)

    assert first["ticket_query"]["total"] == 7
    assert first["ticket_query"]["pagination"]["page_size"] == 5
    assert first["ticket_query"]["pagination"]["total_pages"] == 2
    assert len(first["ticket_query"]["tickets"]) == 5
    assert second["ticket_query"]["pagination"]["page"] == 2
    assert len(second["ticket_query"]["tickets"]) == 2
    assert counted["ticket_query"]["total"] == 7
    assert open_count["ticket_query"]["total"] == 5
    assert ticket_snapshot(engine) == before


def test_status_and_client_waiting_queries_return_only_owner_tickets(
    phase2_client,
) -> None:
    client, engine = phase2_client
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        other = add_other_client(session)
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-PENDING-001",
            status=TicketStatus.PENDIENTE_CLIENTE,
        )
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-PROGRESS-002",
            status=TicketStatus.EN_PROCESO,
        )
        add_ticket(
            session,
            client_id=other.id,
            tracking_code="TCK-OTHER-PENDING-003",
            status=TicketStatus.PENDIENTE_CLIENTE,
        )
        session.commit()
    token = login(client)
    before = ticket_snapshot(engine)

    pending = ask(client, "¿Qué solicitudes están esperando algo de mi parte?", token)
    status = ask(client, "¿En qué estado está el ticket TCK-PENDING-001?", token)

    assert pending["suggested_action"] == "CLIENT_ACTION_REQUIRED"
    assert pending["ticket_query"]["total"] == 1
    assert [item["tracking_code"] for item in pending["ticket_query"]["tickets"]] == [
        "TCK-PENDING-001"
    ]
    assert status["ticket_query"]["ticket"]["status"] == "PENDIENTE_CLIENTE"
    assert ticket_snapshot(engine) == before


def test_code_reply_resumes_the_pending_status_query(phase2_client) -> None:
    client, engine = phase2_client
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-RESUME-001",
            status=TicketStatus.EN_PROCESO,
        )
        session.commit()
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}
    conversation = client.post("/api/v1/chat/conversations", headers=headers).json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    before = ticket_snapshot(engine)

    clarification = client.post(
        endpoint,
        headers=headers,
        json={"content": "¿En qué estado está mi ticket?"},
    )
    resumed = client.post(
        endpoint,
        headers=headers,
        json={"content": "TCK-RESUME-001"},
    )

    assert clarification.json()["suggested_action"] == "CLARIFY_TICKET_CODE"
    assert resumed.status_code == 200
    assert resumed.json()["suggested_action"] == "TICKET_STATUS"
    assert resumed.json()["ticket_query"]["ticket"]["tracking_code"] == (
        "TCK-RESUME-001"
    )
    assert ticket_snapshot(engine) == before


def test_advisor_reply_queries_use_real_author_and_date_and_limit_content(
    phase2_client,
) -> None:
    client, engine = phase2_client
    created = datetime(2026, 10, 9, 10, tzinfo=UTC)
    first_reply_at = created + timedelta(hours=1)
    latest_reply_at = created + timedelta(hours=3)
    customer_comment_at = created + timedelta(hours=4)
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-REPLY-101",
            status=TicketStatus.EN_PROCESO,
            created_at=created,
            comments=(
                ("asesor@demo.com", "Primera respuesta visible", first_reply_at),
                ("asesor@demo.com", "Respuesta más reciente visible", latest_reply_at),
                (
                    "cliente@demo.com",
                    "Gracias, espero la revisión",
                    customer_comment_at,
                ),
            ),
        )
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-NOREPLY-102",
            status=TicketStatus.ASIGNADO,
            created_at=created - timedelta(days=1),
        )
        session.commit()
    token = login(client)
    before = ticket_snapshot(engine)

    status = ask(client, "¿Ya me respondió el asesor?", token)
    reply = ask(client, "¿Qué me respondió?", token)
    no_reply = ask(
        client, "¿Ya me respondió el asesor en el ticket TCK-NOREPLY-102?", token
    )

    assert status["suggested_action"] == "ADVISOR_RESPONSE_STATUS"
    assert status["ticket_query"]["advisor_responded"] is True
    assert status["ticket_query"]["latest_advisor_response"]["content"] is None
    assert status["ticket_query"]["latest_advisor_response"]["created_at"].startswith(
        latest_reply_at.isoformat().replace("+00:00", "")
    )
    assert reply["suggested_action"] == "LATEST_ADVISOR_RESPONSE"
    assert (
        reply["ticket_query"]["latest_advisor_response"]["author_name"] == "Asesor Demo"
    )
    assert reply["ticket_query"]["latest_advisor_response"]["content"] == (
        "Respuesta más reciente visible"
    )
    assert "Primera respuesta visible" not in str(reply)
    assert no_reply["ticket_query"]["advisor_responded"] is False
    assert no_reply["ticket_query"]["latest_advisor_response"] is None
    assert ticket_snapshot(engine) == before


def test_missing_or_ambiguous_codes_return_typed_clarification_without_ai(
    phase2_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = phase2_client
    settings = rag_service_module.get_settings()
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )

    class CapturingProvider:
        name = "test-capturing-provider"
        model = "test"

        def __init__(self) -> None:
            self.requests: list[AIProviderRequest] = []

        def generate(self, request: AIProviderRequest) -> AIGenerationResult:
            self.requests.append(request)
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code="unexpected_ticket_query_provider_call",
            )

    provider = CapturingProvider()
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        ticket_service=TicketService(),
        rag_service=RAGService(provider=provider, runtime=_RuntimeState()),
    )
    token = login(client)

    missing = ask(client, "¿En qué estado está mi ticket?", token)
    malformed = ask(client, "¿En qué estado está el ticket TCK-…?", token)
    multiple = ask(
        client,
        "¿En qué estado están TCK-OWN-001 y TCK-OWN-002?",
        token,
    )

    for response in (missing, malformed, multiple):
        assert response["suggested_action"] == "CLARIFY_TICKET_CODE"
        assert response["ticket_query"]["query_type"] == "CLARIFY_TICKET_CODE"
        assert response["requires_clarification"] is True
        assert response["offers_ticket"] is False
    assert provider.requests == []


def test_ticket_queries_skip_ai_and_openapi_documents_typed_contract(
    phase2_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = phase2_client
    settings = rag_service_module.get_settings()

    class CapturingProvider:
        name = "test-capturing-provider"
        model = "test"

        def __init__(self) -> None:
            self.requests: list[AIProviderRequest] = []

        def generate(self, request: AIProviderRequest) -> AIGenerationResult:
            self.requests.append(request)
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code="unexpected_ticket_query_provider_call",
            )

    provider = CapturingProvider()
    app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
        ticket_service=TicketService(),
        rag_service=RAGService(provider=provider, runtime=_RuntimeState()),
    )
    # Enable provider traffic if the query classifier accidentally falls through.
    monkeypatch.setattr(
        rag_service_module,
        "get_settings",
        lambda: settings.model_copy(update={"chatbot_ai_enabled": True}),
    )
    with Session(engine) as session:
        owner = session.exec(select(User).where(User.email == "cliente@demo.com")).one()
        add_ticket(
            session,
            client_id=owner.id,
            tracking_code="TCK-AI-PRIVATE-001",
            status=TicketStatus.EN_PROCESO,
            comments=(
                (
                    "asesor@demo.com",
                    "Comentario privado para comprobar que no se envía a IA",
                    datetime(2026, 10, 10, 12, tzinfo=UTC),
                ),
            ),
        )
        session.commit()
    token = login(client)
    before = ticket_snapshot(engine)

    headers = {"Authorization": f"Bearer {token}"}
    conversation = client.post("/api/v1/chat/conversations", headers=headers).json()
    endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
    response = client.post(
        endpoint, headers=headers, json={"content": "¿Qué me respondió?"}
    )
    assert response.status_code == 200
    assert response.json()["ticket_query"]["latest_advisor_response"]["content"] == (
        "Comentario privado para comprobar que no se envía a IA"
    )
    assert provider.requests == []
    follow_up_content = "Eso, ¿qué requisitos necesito para una tarjeta?"
    follow_up = client.post(
        endpoint, headers=headers, json={"content": follow_up_content}
    )
    assert follow_up.status_code == 200
    assert provider.requests
    provider_request = provider.requests[-1]
    assert provider_request.user_message == follow_up_content
    assert "TCK-AI-PRIVATE-001" not in str(provider_request)
    assert "Comentario privado para comprobar que no se envía a IA" not in str(
        provider_request
    )
    assert ticket_snapshot(engine) == before
    schema = app.openapi()
    components = schema["components"]["schemas"]
    response_fields = components["BotMessageResponse"]["properties"]
    assert "examples" in components["BotMessageResponse"]
    assert any(
        item.get("$ref", "").endswith("/ChatbotTicketQuery")
        for item in response_fields["ticket_query"].get("anyOf", [])
    )
    assert "examples" in response_fields["ticket_query"]
    assert components["ChatbotTicketQueryType"]["enum"] == [
        "AUTHENTICATION_REQUIRED",
        "TICKET_LIST",
        "TICKET_COUNT",
        "TICKET_STATUS",
        "ADVISOR_RESPONSE_STATUS",
        "ADVISOR_REPLY",
        "CLIENT_ACTION_REQUIRED",
        "LATEST_TICKET",
        "CLARIFY_TICKET_CODE",
    ]
