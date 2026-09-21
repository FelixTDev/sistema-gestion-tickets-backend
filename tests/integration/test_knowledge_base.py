from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def knowledge_client() -> Generator[tuple[TestClient, object], None, None]:
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
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def create_faq(client: TestClient, token: str, category: str, suffix: str) -> dict:
    response = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "title": f"Título {suffix}",
            "question": f"¿Cómo consulto {suffix}?",
            "answer": f"Respuesta autorizada para {suffix}.",
            "summary": f"Resumen de {suffix}",
            "keywords": f"{suffix},consulta",
            "tags": ["operacion", suffix],
            "synonyms": [f"alternativa-{suffix}"],
        },
    )
    assert response.status_code == 201
    return response.json()


def publish_faq(client: TestClient, token: str, faq_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    review = client.patch(
        f"/api/v1/faqs/{faq_id}/workflow",
        headers=headers,
        json={"status": "REVIEW"},
    )
    assert review.status_code == 200
    published = client.patch(
        f"/api/v1/faqs/{faq_id}/workflow",
        headers=headers,
        json={"status": "PUBLISHED"},
    )
    assert published.status_code == 200
    return published.json()


def test_editorial_lifecycle_hides_draft_until_supervisor_publishes(knowledge_client):
    client, engine = knowledge_client
    supervisor = login(client, "supervisor@demo.com")
    faq = create_faq(client, supervisor, category_id(engine), "publicacion")

    assert faq["status"] == "DRAFT"
    assert faq["is_active"] is False
    assert client.get("/api/v1/faqs").json()
    assert faq["id"] not in {item["id"] for item in client.get("/api/v1/faqs").json()}

    published = publish_faq(client, supervisor, faq["id"])
    assert published["status"] == "PUBLISHED"
    assert published["is_active"] is True
    assert any(item["id"] == faq["id"] for item in client.get("/api/v1/faqs").json())
    admin_listing = client.get(
        "/api/v1/faqs/admin",
        headers={"Authorization": f"Bearer {supervisor}"},
    )
    assert admin_listing.status_code == 200
    assert any(item["id"] == faq["id"] for item in admin_listing.json()["items"])
    archived = client.patch(
        f"/api/v1/faqs/{faq['id']}/workflow",
        headers={"Authorization": f"Bearer {supervisor}"},
        json={"status": "ARCHIVED"},
    )
    assert archived.status_code == 200
    assert faq["id"] not in {item["id"] for item in client.get("/api/v1/faqs").json()}


def test_only_supervisor_can_manage_editorial_content_and_admin_listing(
    knowledge_client,
):
    client, engine = knowledge_client
    category = category_id(engine)
    for email in ("cliente@demo.com", "asesor@demo.com"):
        token = login(client, email)
        forbidden = client.post(
            "/api/v1/faqs",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "category_id": category,
                "question": "No autorizado",
                "answer": "No autorizado",
                "keywords": "no",
            },
        )
        assert forbidden.status_code == 403

    assert client.get("/api/v1/faqs/admin").status_code == 401
    client_token = login(client, "cliente@demo.com")
    assert (
        client.get(
            "/api/v1/faqs/admin",
            headers={"Authorization": f"Bearer {client_token}"},
        ).status_code
        == 403
    )


def test_published_update_creates_version_without_losing_previous_content(
    knowledge_client,
):
    client, engine = knowledge_client
    token = login(client, "supervisor@demo.com")
    faq = publish_faq(
        client,
        token,
        create_faq(client, token, category_id(engine), "versionado")["id"],
    )

    updated = client.patch(
        f"/api/v1/faqs/{faq['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"answer": "Nueva respuesta editorial."},
    )
    history = client.get(
        f"/api/v1/faqs/{faq['id']}/history",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 4
    assert updated.json()["status"] == "REVIEW"
    assert history.status_code == 200
    assert len(history.json()) == 4
    assert any(item["answer"] == faq["answer"] for item in history.json())


def test_public_search_supports_normalization_filters_pagination_and_stable_order(
    knowledge_client,
):
    client, engine = knowledge_client
    token = login(client, "supervisor@demo.com")
    first = publish_faq(
        client, token, create_faq(client, token, category_id(engine), "Ácceso")["id"]
    )
    second = publish_faq(
        client,
        token,
        create_faq(client, token, category_id(engine), "Acceso Dos")["id"],
    )

    response = client.get(
        "/api/v1/faqs",
        params={"search": "ACCESO", "page": 1, "page_size": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"page", "page_size", "total", "total_pages", "items"}
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] in {first["id"], second["id"]}
    for term in (
        "titulo acceso",
        "consulto acceso",
        "respuesta autorizada",
        "operacion",
        "alternativa-acceso",
    ):
        found = client.get(
            "/api/v1/faqs", params={"search": term, "page": 1, "page_size": 20}
        )
        assert found.status_code == 200
        assert found.json()["total"] >= 1
    tag_filtered = client.get("/api/v1/faqs", params={"tag": "operacion", "page": 1})
    assert tag_filtered.status_code == 200
    assert tag_filtered.json()["total"] >= 2


def test_duplicate_content_invalid_payload_and_invalid_workflow_are_rejected(
    knowledge_client,
):
    client, engine = knowledge_client
    token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    faq = create_faq(client, token, category, "duplicado")

    duplicate = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "question": faq["question"],
            "answer": "Otra respuesta",
            "keywords": "duplicado",
        },
    )
    empty = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {token}"},
        json={"category_id": category, "question": "   ", "answer": "", "keywords": ""},
    )
    invalid_transition = client.patch(
        f"/api/v1/faqs/{faq['id']}/workflow",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "PUBLISHED"},
    )

    assert duplicate.status_code == 409
    assert empty.status_code == 422
    assert invalid_transition.status_code == 409


def test_knowledge_content_is_stored_as_safe_plain_text(knowledge_client):
    client, engine = knowledge_client
    token = login(client, "supervisor@demo.com")
    response = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category_id(engine),
            "question": "¿Qué es una consulta segura?",
            "answer": "<script>alert('x')</script>Respuesta segura",
            "keywords": "segura",
        },
    )

    assert response.status_code == 201
    assert "<script>" not in response.json()["answer"]
    assert "&lt;script&gt;" in response.json()["answer"]


def test_feedback_is_allowed_publicly_and_metrics_are_aggregated_for_supervisor(
    knowledge_client,
):
    client, engine = knowledge_client
    supervisor = login(client, "supervisor@demo.com")
    faq = publish_faq(
        client,
        supervisor,
        create_faq(client, supervisor, category_id(engine), "feedback")["id"],
    )

    anonymous = client.post(
        f"/api/v1/faqs/{faq['id']}/feedback",
        json={"is_helpful": True, "comment": "La explicación fue útil."},
    )
    client_feedback = client.post(
        f"/api/v1/faqs/{faq['id']}/feedback",
        headers={"Authorization": f"Bearer {login(client, 'cliente@demo.com')}"},
        json={"is_helpful": False},
    )
    metrics = client.get(
        "/api/v1/faqs/admin/metrics/utility",
        headers={"Authorization": f"Bearer {supervisor}"},
    )

    assert anonymous.status_code == 201
    assert client_feedback.status_code == 201
    assert metrics.status_code == 200
    assert metrics.json()["total_feedback"] == 2
    assert metrics.json()["helpful"] == 1
    assert metrics.json()["not_helpful"] == 1
    assert "user_id" not in metrics.json()


def test_chatbot_ignores_faqs_from_inactive_categories(knowledge_client):
    client, engine = knowledge_client
    faq = engine
    with Session(engine) as session:
        record = session.exec(
            select(FAQ).where(FAQ.question.like("%requisitos%"))
        ).first()
        assert record is not None
        category = session.get(TicketCategory, record.category_id)
        assert category is not None
        category.is_active = False
        session.add(category)
        session.commit()
        conversation_id = client.post("/api/v1/chat/conversations").json()["id"]

    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        json={"content": "tarjetas requisitos"},
    )

    assert response.status_code == 200
    assert response.json()["resolved"] is False
    assert faq is not None


@pytest.mark.parametrize(
    "path", ["/api/v1/faqs/admin", "/api/v1/faqs/admin/metrics/utility"]
)
def test_admin_knowledge_endpoints_require_authentication(knowledge_client, path: str):
    client, _ = knowledge_client
    assert client.get(path).status_code == 401
