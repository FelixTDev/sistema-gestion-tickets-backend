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
        "¡Hola! Soy el asistente virtual del prototipo académico de gestión de "
        "tickets de Banco GNB Perú. No es un canal bancario oficial. ¿En qué "
        "puedo ayudarte hoy? Puedo orientarte sobre preguntas frecuentes o el "
        "seguimiento de una solicitud."
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


def test_greeting_identifies_academic_prototype_and_non_official_channel(
    chatbot_client,
):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "hola"},
    )

    assert response.status_code == 200
    answer = response.json()["bot_message"]["content"].casefold()
    assert "prototipo académico" in answer
    assert "no es un canal bancario oficial" in answer
    assert response.json()["conversation_status"] == "ACTIVE"


def test_greeting_with_a_real_question_uses_the_normal_query_flow(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "Hola, no puedo ingresar a mi cuenta"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "prototipo académico" not in body["bot_message"]["content"]
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
        "¿Qué preguntas te puedo hacer?",
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
    assert 1 <= len(options) <= 5

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


def test_faq_discovery_accepts_ordinal_follow_up(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    discovery = client.post(endpoint, json={"content": "¿Qué preguntas me puedes dar?"})
    first_option = discovery.json()["clarification_options"][0]
    answer = client.post(endpoint, json={"content": "la primera"})

    assert answer.status_code == 200
    body = answer.json()
    assert body["resolved"] is True
    assert body["requires_clarification"] is False
    assert body["faq_id"] is not None
    assert body["bot_message"]["content"]
    assert any(source["title"] == first_option for source in body["sources"])


def test_faq_discovery_exposes_stable_server_resolvable_option_ids(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    discovery = client.post(endpoint, json={"content": "Preguntas frecuentes"})

    assert discovery.status_code == 200
    body = discovery.json()
    assert body["ui"]["type"] == "FAQ_CHOICES"
    assert body["ui"]["menu_id"] == "faq_discovery"
    assert [option["label"] for option in body["ui"]["options"]] == body[
        "clarification_options"
    ]
    assert all(option["action"] == "SELECT_FAQ" for option in body["ui"]["options"])
    option = body["ui"]["options"][0]
    assert option["id"].startswith("faq:")

    answer = client.post(endpoint, json={"option_id": option["id"]})

    assert answer.status_code == 200
    answer_body = answer.json()
    assert answer_body["faq_id"] == option["id"].removeprefix("faq:")
    assert answer_body["resolved"] is True
    assert answer_body["sources"][0]["title"] == option["label"]


def test_button_ids_are_accepted_only_when_offered_in_current_state(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    menu = client.post(endpoint, json={"content": "mostrar menu"})
    assert menu.status_code == 200
    assert menu.json()["ui"]["type"] == "MAIN_MENU"
    assert (
        client.get(f"/api/v1/chat/conversations/{conversation_id}").json()["menu_open"]
        is True
    )

    rejected = client.post(endpoint, json={"option_id": "menu:delete_conversation"})
    assert rejected.status_code == 409
    conversation = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()
    assert len(conversation["messages"]) == 2
    assert conversation["menu_open"] is True

    selected = client.post(endpoint, json={"option_id": "menu:faq"})
    assert selected.status_code == 200
    selected_body = selected.json()
    assert selected_body["bot_message"]["intent"] == "FAQ_CATEGORY_DISCOVERY"
    assert selected_body["ui"]["type"] == "FAQ_CATEGORY_CHOICES"
    assert selected_body["menu_open"] is False


def test_message_accepts_exactly_one_text_or_option_id(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    both = client.post(
        endpoint,
        json={"content": "hola", "option_id": "menu:faq"},
    )
    neither = client.post(endpoint, json={})

    assert both.status_code == 422
    assert neither.status_code == 422
    conversation = client.get(f"/api/v1/chat/conversations/{conversation_id}")
    assert conversation.json()["messages"] == []


def test_main_menu_is_separate_from_faq_discovery_and_restorable(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    response = client.post(endpoint, json={"content": "mostrar menu"})

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_action"] == "SHOW_MENU"
    assert body["bot_message"]["intent"] == "CONVERSATION_MENU"
    assert body["clarification_options"] == []
    assert body["sources"] == []
    assert body["ui"]["type"] == "MAIN_MENU"
    assert body["ui"]["menu_id"] == "main"
    assert {option["id"] for option in body["ui"]["options"]} >= {
        "menu:faq",
        "menu:tickets",
        "menu:create_ticket",
        "menu:advisor",
        "menu:end",
    }

    restored = client.get(f"/api/v1/chat/conversations/{conversation_id}")
    assert restored.status_code == 200
    assert restored.json()["ui"] == body["ui"]


def test_faq_menu_browses_real_categories_then_questions(chatbot_client):
    client, engine = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"
    client.post(endpoint, json={"content": "mostrar menu"})

    categories = client.post(endpoint, json={"option_id": "menu:faq"})

    assert categories.status_code == 200
    category_body = categories.json()
    assert category_body["bot_message"]["intent"] == "FAQ_CATEGORY_DISCOVERY"
    assert category_body["ui"]["type"] == "FAQ_CATEGORY_CHOICES"
    assert category_body["sources"] == []
    assert any(option["id"] == "nav:menu" for option in category_body["ui"]["options"])
    category_options = [
        option
        for option in category_body["ui"]["options"]
        if option["action"] == "SHOW_FAQ_CATEGORY"
    ]
    assert category_options
    assert all(option["id"].startswith("category:") for option in category_options)
    category_state = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()
    assert category_state["ui"] == category_body["ui"]
    with Session(engine) as session:
        active_categories = {
            faq.category_id
            for faq in session.exec(
                select(FAQ).where(FAQ.status == FAQ.Status.PUBLISHED, FAQ.is_active)
            ).all()
        }
    selected_category_id = category_options[0]["id"].removeprefix("category:")
    assert selected_category_id in active_categories

    subcategories = client.post(
        endpoint,
        json={"option_id": category_options[0]["id"]},
    )

    assert subcategories.status_code == 200
    subcategory_body = subcategories.json()
    assert subcategory_body["bot_message"]["intent"] == "FAQ_SUBCATEGORY_DISCOVERY"
    assert subcategory_body["ui"]["type"] == "FAQ_SUBCATEGORY_CHOICES"
    subcategory_options = [
        option
        for option in subcategory_body["ui"]["options"]
        if option["action"] == "SHOW_FAQ_SUBCATEGORY"
    ]
    assert subcategory_options

    questions = client.post(
        endpoint,
        json={"option_id": subcategory_options[0]["id"]},
    )

    assert questions.status_code == 200
    question_body = questions.json()
    assert question_body["bot_message"]["intent"] == "FAQ_DISCOVERY"
    assert question_body["ui"]["type"] == "FAQ_CHOICES"
    question_options = [
        option
        for option in question_body["ui"]["options"]
        if option["action"] == "SELECT_FAQ"
    ]
    assert question_body["sources"]
    assert all(option["id"].startswith("faq:") for option in question_options)
    assert {"nav:back", "nav:menu"} <= {
        option["id"] for option in question_body["ui"]["options"]
    }
    with Session(engine) as session:
        available_questions = {
            faq.title or faq.question
            for faq in session.exec(
                select(FAQ).where(
                    FAQ.category_id == selected_category_id,
                    FAQ.status == FAQ.Status.PUBLISHED,
                    FAQ.is_active,
                )
            ).all()
        }
    assert {source["title"] for source in question_body["sources"]} <= (
        available_questions
    )


def _open_faq_category_choices(client: TestClient) -> tuple[str, dict]:
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"
    client.post(endpoint, json={"content": "mostrar menu"})
    response = client.post(endpoint, json={"option_id": "menu:faq"})
    assert response.status_code == 200
    return endpoint, response.json()


def test_faq_category_drills_into_verified_subcategories(chatbot_client):
    client, engine = chatbot_client
    endpoint, categories = _open_faq_category_choices(client)
    tarjetas_id = next(
        option["id"]
        for option in categories["ui"]["options"]
        if option["label"] == "TARJETAS"
    )

    subcategories = client.post(endpoint, json={"option_id": tarjetas_id})

    assert subcategories.status_code == 200
    subcategory_body = subcategories.json()
    assert subcategory_body["bot_message"]["intent"] == "FAQ_SUBCATEGORY_DISCOVERY"
    assert subcategory_body["ui"]["type"] == "FAQ_SUBCATEGORY_CHOICES"
    assert any(
        option["action"] == "SHOW_FAQ_SUBCATEGORY"
        for option in subcategory_body["ui"]["options"]
    )
    assert any(
        option["id"] == "nav:back" for option in subcategory_body["ui"]["options"]
    )
    assert any(
        option["id"] == "nav:menu" for option in subcategory_body["ui"]["options"]
    )
    assert all(
        source["url"].startswith("https://www.bancognb.com.pe/")
        for source in subcategory_body["sources"]
    )

    first_subcategory = next(
        option
        for option in subcategory_body["ui"]["options"]
        if option["action"] == "SHOW_FAQ_SUBCATEGORY"
    )
    questions = client.post(endpoint, json={"option_id": first_subcategory["id"]})

    assert questions.status_code == 200
    question_body = questions.json()
    assert question_body["bot_message"]["intent"] == "FAQ_DISCOVERY"
    assert question_body["ui"]["type"] == "FAQ_CHOICES"
    question_options = [
        option
        for option in question_body["ui"]["options"]
        if option["action"] == "SELECT_FAQ"
    ]
    assert question_options
    assert all(option["id"].startswith("faq:") for option in question_options)
    assert {"nav:back", "nav:menu"} <= {
        option["id"] for option in question_body["ui"]["options"]
    }
    assert all(source["category"] == "TARJETAS" for source in question_body["sources"])
    with Session(engine) as session:
        faq_ids = {
            faq.id
            for faq in session.exec(
                select(FAQ).where(
                    FAQ.category_id == tarjetas_id.removeprefix("category:"),
                    FAQ.status == FAQ.Status.PUBLISHED,
                    FAQ.is_active,
                )
            ).all()
        }
    assert all(
        option["id"].removeprefix("faq:") in faq_ids for option in question_options
    )


def test_faq_buttons_navigate_back_and_to_main_menu(chatbot_client):
    client, _ = chatbot_client
    endpoint, categories = _open_faq_category_choices(client)
    tarjetas_id = next(
        option["id"]
        for option in categories["ui"]["options"]
        if option["label"] == "TARJETAS"
    )
    subcategories = client.post(endpoint, json={"option_id": tarjetas_id})
    assert subcategories.status_code == 200

    back = client.post(endpoint, json={"option_id": "nav:back"})

    assert back.status_code == 200
    assert back.json()["ui"]["type"] == "FAQ_CATEGORY_CHOICES"

    main_menu = client.post(endpoint, json={"option_id": "nav:menu"})

    assert main_menu.status_code == 200
    assert main_menu.json()["suggested_action"] == "SHOW_MENU"
    assert main_menu.json()["ui"]["type"] == "MAIN_MENU"


def test_invalid_faq_navigation_option_does_not_mutate_conversation(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"
    before = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()

    invalid = client.post(endpoint, json={"option_id": "nav:back"})

    assert invalid.status_code == 409
    after = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()
    assert after["status"] == before["status"]
    assert after["messages"] == before["messages"]


@pytest.mark.parametrize(
    "content",
    ["menú", "mostrar menú", "volver al menú", "regresar a las opciones"],
)
def test_explicit_menu_request_returns_show_menu(chatbot_client, content: str):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["suggested_action"] == "SHOW_MENU"
    assert body["bot_message"]["intent"] == "CONVERSATION_MENU"
    assert body["menu_open"] is True
    assert body["conversation_status"] == "ACTIVE"
    assert body["clarification_options"] == []
    assert body["sources"] == []
    assert body["ui"]["type"] == "MAIN_MENU"


@pytest.mark.parametrize("content", ["otra consulta", "no me ayudó", "no entendí"])
def test_non_menu_follow_up_does_not_emit_show_menu(chatbot_client, content: str):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": content},
    )

    assert response.status_code == 200
    assert response.json()["suggested_action"] != "SHOW_MENU"


def test_generic_unmatched_query_does_not_resolve_from_common_words(chatbot_client):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "consulta que no existe"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is False
    assert body["faq_id"] is None
    assert body["requires_clarification"] is True
    assert body["offers_ticket"] is False


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
    assert response.json()["clarification_options"]
    assert response.json()["available_actions"] == [
        "BROWSE_KNOWLEDGE",
        "CREATE_TICKET",
        "CONTACT_ADVISOR",
    ]


def test_repeated_unknown_query_returns_verified_options_and_next_actions(
    chatbot_client,
):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"

    for _ in range(3):
        response = client.post(
            endpoint,
            json={"content": "necesito ayuda con un trámite que no aparece"},
        )
        assert response.status_code == 200

    body = response.json()
    assert body["resolved"] is False
    assert body["fallback_reason"] == "LOW_CONFIDENCE"
    assert body["clarification_options"]
    assert body["available_actions"] == [
        "BROWSE_KNOWLEDGE",
        "CREATE_TICKET",
        "CONTACT_ADVISOR",
        "VIEW_MY_TICKETS",
    ]
    assert all(source["url"].startswith("https://") for source in body["sources"])


def test_unhelpful_follow_up_accepts_fused_words_and_offers_real_next_steps(
    chatbot_client,
):
    client, _ = chatbot_client
    conversation_id = client.post("/api/v1/chat/conversations").json()["id"]
    endpoint = f"/api/v1/chat/conversations/{conversation_id}/messages"
    client.post(endpoint, json={"content": "requisitos tarjeta"})

    response = client.post(endpoint, json={"content": "no resuelvesmi duda"})

    assert response.status_code == 200
    body = response.json()
    assert body["bot_message"]["intent"] == "FOLLOW_UP_UNRESOLVED"
    assert "no resolvió" in body["bot_message"]["content"].casefold()
    assert "CONTACT_ADVISOR" in body["available_actions"]
    assert "CREATE_TICKET" in body["available_actions"]


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
