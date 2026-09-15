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
from app.modules.tickets.models.ticket import Ticket, TicketStatus
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def admin_client() -> Generator[tuple[TestClient, object], None, None]:
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


def supervisor_headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"}


def category_id(engine: object) -> str:
    with Session(engine) as session:
        return session.exec(select(TicketCategory)).first().id


def test_supervisor_can_create_update_and_toggle_faq(admin_client):
    client, engine = admin_client
    headers = supervisor_headers(client)
    category = category_id(engine)

    created = client.post(
        "/api/v1/faqs",
        headers=headers,
        json={
            "category_id": category,
            "question": "¿Cómo actualizo mis datos?",
            "answer": "Puedes actualizarlos en una oficina ficticia.",
            "keywords": "actualizar,datos",
        },
    )
    faq_id = created.json()["id"]
    updated = client.patch(
        f"/api/v1/faqs/{faq_id}",
        headers=headers,
        json={"answer": "Respuesta actualizada."},
    )
    disabled = client.patch(
        f"/api/v1/faqs/{faq_id}/status",
        headers=headers,
        json={"is_active": False},
    )

    assert created.status_code == 201
    assert created.json()["created_by"]
    assert updated.status_code == 200
    assert updated.json()["answer"] == "Respuesta actualizada."
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False


@pytest.mark.parametrize("email", ["cliente@demo.com", "asesor@demo.com"])
def test_client_and_advisor_cannot_manage_faq(admin_client, email: str):
    client, engine = admin_client
    category = category_id(engine)
    response = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {login(client, email)}"},
        json={
            "category_id": category,
            "question": "No autorizado",
            "answer": "No autorizado",
            "keywords": "no",
        },
    )

    assert response.status_code == 403


def test_public_faq_reads_only_active_records(admin_client):
    client, engine = admin_client
    headers = supervisor_headers(client)
    created = client.post(
        "/api/v1/faqs",
        headers=headers,
        json={
            "category_id": category_id(engine),
            "question": "FAQ temporal",
            "answer": "Respuesta temporal",
            "keywords": "temporal",
        },
    ).json()
    client.patch(
        f"/api/v1/faqs/{created['id']}/status",
        headers=headers,
        json={"is_active": False},
    )

    listing = client.get("/api/v1/faqs")
    detail = client.get(f"/api/v1/faqs/{created['id']}")

    assert listing.status_code == 200
    assert all(item["is_active"] for item in listing.json())
    assert detail.status_code == 404


def test_supervisor_can_crud_category_and_duplicate_names_are_rejected(admin_client):
    client, _ = admin_client
    headers = supervisor_headers(client)
    payload = {"name": "NUEVA_CATEGORIA", "description": "Categoría nueva"}
    created = client.post("/api/v1/categories", headers=headers, json=payload)
    duplicate = client.post("/api/v1/categories", headers=headers, json=payload)
    updated = client.patch(
        f"/api/v1/categories/{created.json()['id']}",
        headers=headers,
        json={"description": "Descripción editada"},
    )
    disabled = client.patch(
        f"/api/v1/categories/{created.json()['id']}/status",
        headers=headers,
        json={"is_active": False},
    )

    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert updated.status_code == 200
    assert disabled.status_code == 200


def test_category_management_is_supervisor_only(admin_client):
    client, _ = admin_client
    for email in ("cliente@demo.com", "asesor@demo.com"):
        response = client.post(
            "/api/v1/categories",
            headers={"Authorization": f"Bearer {login(client, email)}"},
            json={"name": f"NO_{email}", "description": "No"},
        )
        assert response.status_code == 403


def test_cannot_disable_category_used_by_ticket(admin_client):
    client, engine = admin_client
    headers = supervisor_headers(client)
    client_token = login(client, "cliente@demo.com")
    category = category_id(engine)
    ticket = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {client_token}"},
        json={
            "category_id": category,
            "subject": "Usa categoría",
            "description": "Ticket de prueba para proteger categoría.",
            "priority": "MEDIA",
        },
    )
    response = client.patch(
        f"/api/v1/categories/{category}/status",
        headers=headers,
        json={"is_active": False},
    )

    assert ticket.status_code == 201
    assert response.status_code == 409


def create_report_ticket(
    client: TestClient, category: str, priority: str = "MEDIA"
) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {login(client, 'cliente@demo.com')}"},
        json={
            "category_id": category,
            "subject": "Ticket para reporte",
            "description": "Datos de prueba para el dashboard.",
            "priority": priority,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_reports_are_supervisor_only_and_return_correct_totals(admin_client):
    client, engine = admin_client
    category = category_id(engine)
    create_report_ticket(client, category, "ALTA")
    supervisor = supervisor_headers(client)

    client_response = client.get(
        "/api/v1/reports/summary",
        headers={"Authorization": f"Bearer {login(client, 'cliente@demo.com')}"},
    )
    summary = client.get("/api/v1/reports/summary", headers=supervisor)
    by_status = client.get("/api/v1/reports/by-status", headers=supervisor)
    by_priority = client.get("/api/v1/reports/by-priority", headers=supervisor)

    assert client_response.status_code == 403
    assert summary.status_code == 200
    assert summary.json()["total_tickets"] == 1
    assert summary.json()["new_tickets"] == 1
    assert by_status.json()["items"]
    assert any(item["priority"] == "ALTA" for item in by_priority.json()["items"])


def test_reports_support_filters_and_empty_results(admin_client):
    client, engine = admin_client
    category = category_id(engine)
    ticket = create_report_ticket(client, category, "URGENTE")
    supervisor = supervisor_headers(client)
    client.post(
        f"/api/v1/tickets/{ticket['id']}/status",
        headers=supervisor,
        json={"status": "ASIGNADO"},
    )

    filtered = client.get(
        "/api/v1/reports/summary",
        headers=supervisor,
        params={"category_id": category, "status": "ASIGNADO", "priority": "URGENTE"},
    )
    empty = client.get(
        "/api/v1/reports/by-status",
        headers=supervisor,
        params={"from": "2099-01-01T00:00:00Z", "to": "2099-01-02T00:00:00Z"},
    )
    invalid_range = client.get(
        "/api/v1/reports/summary",
        headers=supervisor,
        params={"from": "2025-02-01T00:00:00Z", "to": "2025-01-01T00:00:00Z"},
    )

    assert filtered.status_code == 200
    assert filtered.json()["total_tickets"] == 1
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert invalid_range.status_code == 422


def test_resolution_time_report_calculates_average_hours(admin_client):
    client, engine = admin_client
    category = category_id(engine)
    first = create_report_ticket(client, category)
    second = create_report_ticket(client, category)
    now = datetime.now(UTC)
    with Session(engine) as session:
        first_model = session.get(Ticket, first["id"])
        second_model = session.get(Ticket, second["id"])
        first_model.created_at = now - timedelta(hours=4)
        first_model.resolved_at = now - timedelta(hours=2)
        first_model.status = TicketStatus.RESUELTO
        second_model.created_at = now - timedelta(hours=6)
        second_model.resolved_at = now - timedelta(hours=2)
        second_model.status = TicketStatus.RESUELTO
        session.commit()

    response = client.get(
        "/api/v1/reports/resolution-time", headers=supervisor_headers(client)
    )

    assert response.status_code == 200
    assert response.json()["resolved_tickets"] == 2
    assert response.json()["average_resolution_time_hours"] == pytest.approx(3.0)
