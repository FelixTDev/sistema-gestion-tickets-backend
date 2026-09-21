from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def export_client() -> Generator[tuple[TestClient, object], None, None]:
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


def headers(client: TestClient, email: str = "supervisor@demo.com") -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, email)}"}


def create_ticket(client: TestClient, engine: object, subject: str) -> dict:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    response = client.post(
        "/api/v1/tickets",
        headers=headers(client, "cliente@demo.com"),
        json={
            "category_id": category.id,
            "subject": subject,
            "description": "Descripción exportable",
            "priority": "ALTA",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_csv_summary_has_safe_headers_and_audit(export_client):
    client, engine = export_client
    create_ticket(client, engine, "=SUM(1,2)")

    response = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "csv"},
        headers=headers(client),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert "report_summary_" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert "password_hash" not in response.text
    assert "access_token" not in response.text
    with Session(engine) as session:
        audit_count = session.execute(
            text("SELECT COUNT(*) FROM report_export_audits")
        ).scalar_one()
    assert audit_count == 1


def test_created_tickets_csv_escapes_formula_cells_and_filters_dates(export_client):
    client, engine = export_client
    ticket = create_ticket(client, engine, "=SUM(1,2)")
    created_at = datetime.fromisoformat(ticket["created_at"].replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    response = client.get(
        "/api/v1/reports/created-tickets/export",
        params={
            "format": "csv",
            "from": (created_at - timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "to": (created_at + timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
        },
        headers=headers(client),
    )

    assert response.status_code == 200, response.text
    assert "'=SUM(1,2)" in response.text
    assert "subject" in response.text


@pytest.mark.parametrize(
    "report_name",
    [
        "by-status",
        "by-priority",
        "by-category",
        "by-source",
        "by-advisor",
        "created-tickets",
        "resolved-tickets",
        "first-response-time",
        "resolution-time",
        "sla-compliance",
        "conversations",
        "faq-utility",
        "operational-activity",
    ],
)
def test_all_export_report_names_return_csv(export_client, report_name: str):
    client, _ = export_client
    response = client.get(
        f"/api/v1/reports/{report_name}/export",
        params={"format": "csv"},
        headers=headers(client),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


def test_export_rejects_invalid_range_format_and_excessive_range(export_client):
    client, engine = export_client
    invalid_range = client.get(
        "/api/v1/reports/summary/export",
        params={
            "format": "csv",
            "from": "2026-02-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
        },
        headers=headers(client),
    )
    excessive_range = client.get(
        "/api/v1/reports/summary/export",
        params={
            "format": "csv",
            "from": "2020-01-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
        },
        headers=headers(client),
    )
    invalid_format = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "pdf"},
        headers=headers(client),
    )
    unsupported_xlsx = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "xlsx"},
        headers=headers(client),
    )

    assert invalid_range.status_code == 422
    assert excessive_range.status_code == 422
    assert invalid_format.status_code == 422
    assert unsupported_xlsx.status_code == 422
    with Session(engine) as session:
        failures = session.execute(
            text("SELECT COUNT(*) FROM report_export_audits WHERE succeeded = 0")
        ).scalar_one()
    assert failures >= 1


def test_export_is_supervisor_only_and_requires_authentication(export_client):
    client, _ = export_client

    unauthenticated = client.get(
        "/api/v1/reports/summary/export", params={"format": "csv"}
    )
    client_response = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "csv"},
        headers=headers(client, "cliente@demo.com"),
    )
    advisor_response = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "csv"},
        headers=headers(client, "asesor@demo.com"),
    )

    assert unauthenticated.status_code == 401
    assert client_response.status_code == 403
    assert advisor_response.status_code == 403


def test_report_filters_are_utc_and_no_results_are_safe(export_client):
    client, _ = export_client
    response = client.get(
        "/api/v1/reports/by-status/export",
        params={
            "format": "csv",
            "from": "2099-01-01T00:00:00+00:00",
            "to": "2099-01-02T23:59:59+00:00",
            "status": "RESUELTO",
            "priority": "URGENTE",
        },
        headers=headers(client),
    )

    assert response.status_code == 200
    assert "RESUELTO" in response.text
