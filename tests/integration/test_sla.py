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
from app.modules.notificaciones.models.notification import Notification
from app.modules.tickets.models.sla import SlaPolicy, SlaPolicyHistory, TicketSla
from app.modules.tickets.models.ticket import Ticket, TicketPriority, TicketSource
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def sla_client() -> Generator[tuple[TestClient, object], None, None]:
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


def login(client: TestClient, email: str, password: str = "demo-password-local") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def create_ticket(
    client: TestClient, token: str, category: str, priority: str = "MEDIA"
) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "subject": "Consulta con seguimiento SLA",
            "description": "Necesito orientación sobre el plazo de atención.",
            "priority": priority,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_creation_applies_priority_policy_and_returns_utc_sla(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine), "URGENTE")

    response = client.get(
        f"/api/v1/tickets/{ticket['id']}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["policy_priority"] == "URGENTE"
    assert datetime.fromisoformat(body["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(body["resolution_due_at"]) > datetime.fromisoformat(
        body["started_at"]
    )
    assert "password_hash" not in str(body)
    assert "storage_key" not in str(body)


def test_category_policy_overrides_priority_and_manual_change_is_audited(sla_client):
    client, engine = sla_client
    supervisor_token = login(client, "supervisor@demo.com")
    client_token = login(client, "cliente@demo.com")
    category = category_id(engine)
    policy = client.post(
        "/api/v1/sla/policies",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={
            "priority": "MEDIA",
            "category_id": category,
            "first_response_seconds": 60,
            "resolution_seconds": 300,
            "warning_seconds": 30,
            "timezone_name": "UTC",
            "calendar_name": "24x7",
        },
    )
    assert policy.status_code == 201

    ticket = create_ticket(client, client_token, category, "MEDIA")
    sla = client.get(
        f"/api/v1/tickets/{ticket['id']}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert sla.status_code == 200
    assert sla.json()["policy_id"] == policy.json()["id"]
    assert sla.json()["resolution_due_at"] != sla.json()["started_at"]

    updated = client.patch(
        f"/api/v1/sla/policies/{policy.json()['id']}",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"resolution_seconds": 600},
    )
    assert updated.status_code == 200
    assert updated.json()["resolution_seconds"] == 600
    with Session(engine) as session:
        audit_rows = session.exec(
            select(SlaPolicyHistory).where(
                SlaPolicyHistory.policy_id == policy.json()["id"]
            )
        ).all()
    assert {row.action for row in audit_rows} == {"CREATED", "UPDATED"}


def test_only_staff_comment_counts_as_first_response(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_response = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    advisor_token = advisor_response.json()["access_token"]
    advisor_id = advisor_response.json()["user"]["id"]
    ticket = create_ticket(client, client_token, category_id(engine))

    client_comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"content": "Agrego información adicional."},
    )
    assert client_comment.status_code == 201
    before = client.get(
        f"/api/v1/tickets/{ticket['id']}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    ).json()
    assert before["first_responded_at"] is None

    assigned = client.post(
        f"/api/v1/tickets/{ticket['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert assigned.status_code == 201
    advisor_comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Estoy revisando tu solicitud."},
    )
    assert advisor_comment.status_code == 201
    after = client.get(
        f"/api/v1/tickets/{ticket['id']}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    ).json()
    assert after["first_responded_at"] is not None
    assert after["first_response_within_sla"] is True


def test_late_first_response_and_resolution_emit_breach_notifications(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_response = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    advisor_token = advisor_response.json()["access_token"]
    advisor_id = advisor_response.json()["user"]["id"]
    category = category_id(engine)

    first_response_ticket = create_ticket(client, client_token, category)
    assert (
        client.post(
            f"/api/v1/tickets/{first_response_ticket['id']}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_id},
        ).status_code
        == 201
    )
    with Session(engine) as session:
        sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == first_response_ticket["id"])
        ).one()
        sla.first_response_due_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(sla)
        session.commit()
    late_comment = client.post(
        f"/api/v1/tickets/{first_response_ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Respuesta posterior al vencimiento."},
    )
    assert late_comment.status_code == 201

    resolution_ticket = create_ticket(client, client_token, category)
    assert (
        client.post(
            f"/api/v1/tickets/{resolution_ticket['id']}/assignments",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"advisor_id": advisor_id},
        ).status_code
        == 201
    )
    for status_value in ("EN_PROCESO",):
        assert (
            client.post(
                f"/api/v1/tickets/{resolution_ticket['id']}/status",
                headers={"Authorization": f"Bearer {supervisor_token}"},
                json={"status": status_value},
            ).status_code
            == 200
        )
    timely_comment = client.post(
        f"/api/v1/tickets/{resolution_ticket['id']}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Primera respuesta dentro del plazo."},
    )
    assert timely_comment.status_code == 201
    with Session(engine) as session:
        sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == resolution_ticket["id"])
        ).one()
        sla.resolution_due_at = datetime.now(UTC) - timedelta(minutes=1)
        session.add(sla)
        session.commit()
    resolved = client.post(
        f"/api/v1/tickets/{resolution_ticket['id']}/status",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"status": "RESUELTO"},
    )
    assert resolved.status_code == 200

    with Session(engine) as session:
        breach_count = len(
            session.exec(
                select(Notification).where(
                    Notification.notification_type == "sla_breached",
                    Notification.related_ticket_id.in_(
                        [first_response_ticket["id"], resolution_ticket["id"]]
                    ),
                )
            ).all()
        )
    assert breach_count == 2


def test_pause_resume_is_explicit_and_auditable(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    url = f"/api/v1/tickets/{ticket['id']}/sla"

    invalid = client.post(
        f"{url}/pause",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={},
    )
    paused = client.post(
        f"{url}/pause",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Esperando información del cliente."},
    )
    resumed = client.post(
        f"{url}/resume",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    assert invalid.status_code == 422
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ACTIVE"
    assert resumed.json()["total_paused_seconds"] >= 0


def test_evaluation_warns_breaches_and_is_idempotent(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    ticket = create_ticket(client, client_token, category_id(engine))
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

    with Session(engine) as session:
        sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == ticket["id"])
        ).one()
        sla.first_response_due_at = now + timedelta(hours=1)
        sla.resolution_due_at = now + timedelta(days=1)
        session.add(sla)
        session.commit()

    from app.modules.tickets.services.sla_service import SlaService

    with Session(engine) as session:
        result = SlaService(clock=lambda: now).evaluate(session)
        session.commit()
        warning_count = len(
            session.exec(
                select(Notification).where(
                    Notification.related_ticket_id == ticket["id"],
                    Notification.notification_type == "sla_warning",
                )
            ).all()
        )
    assert result.warnings == 1
    assert warning_count == 1

    with Session(engine) as session:
        sla = session.exec(
            select(TicketSla).where(TicketSla.ticket_id == ticket["id"])
        ).one()
        sla.first_response_due_at = now - timedelta(hours=1)
        sla.resolution_due_at = now - timedelta(hours=1)
        session.add(sla)
        session.commit()

    with Session(engine) as session:
        first = SlaService(clock=lambda: now).evaluate(session)
        session.commit()
        second = SlaService(clock=lambda: now).evaluate(session)
        session.commit()
        breaches = session.exec(
            select(Notification).where(
                Notification.related_ticket_id == ticket["id"],
                Notification.notification_type == "sla_breached",
            )
        ).all()
    assert first.breaches >= 1
    assert second.warnings == 0
    assert second.breaches == 0
    assert len(breaches) == first.breaches
    assert (
        client.get(
            f"/api/v1/tickets/{ticket['id']}/sla",
            headers={"Authorization": f"Bearer {client_token}"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/sla/evaluate",
            headers={"Authorization": f"Bearer {supervisor_token}"},
        ).status_code
        == 200
    )


def test_terminal_states_stop_and_reopen_continues_existing_clock(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    category = category_id(engine)
    ticket = create_ticket(client, client_token, category)
    ticket_id = ticket["id"]
    for status in ("ASIGNADO", "EN_PROCESO", "RESUELTO"):
        response = client.post(
            f"/api/v1/tickets/{ticket_id}/status",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={"status": status},
        )
        assert response.status_code == 200
    completed = client.get(
        f"/api/v1/tickets/{ticket_id}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    ).json()
    assert completed["first_response_within_sla"] is False
    reopened = client.post(
        f"/api/v1/tickets/{ticket_id}/reopen",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Se requiere una revisión adicional."},
    )
    after_reopen = client.get(
        f"/api/v1/tickets/{ticket_id}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    ).json()
    assert reopened.status_code == 200
    assert after_reopen["status"] == "ACTIVE"
    assert after_reopen["started_at"] == completed["started_at"]
    assert after_reopen["resolution_due_at"] == completed["resolution_due_at"]

    cancelled = create_ticket(client, client_token, category)
    cancelled_response = client.post(
        f"/api/v1/tickets/{cancelled['id']}/cancel",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Solicitud cancelada."},
    )
    cancelled_sla = client.get(
        f"/api/v1/tickets/{cancelled['id']}/sla",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert cancelled_response.status_code == 200
    assert cancelled_sla.json()["status"] == "CANCELLED"


def test_sla_permissions_and_validation(sla_client):
    client, engine = sla_client
    owner_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_token = login(client, "asesor@demo.com")
    ticket = create_ticket(client, owner_token, category_id(engine))
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cliente SLA externo",
            "email": "sla.other@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_token = login(client, "sla.other@example.com", "Demo-password-123")

    assert client.get(f"/api/v1/tickets/{ticket['id']}/sla").status_code == 401
    assert (
        client.get(
            f"/api/v1/tickets/{ticket['id']}/sla",
            headers={"Authorization": f"Bearer {advisor_token}"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/tickets/{ticket['id']}/sla",
            headers={"Authorization": f"Bearer {other_token}"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/tickets/missing-ticket/sla",
            headers={"Authorization": f"Bearer {owner_token}"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/sla/policies",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "priority": "BAJA",
                "first_response_seconds": 60,
                "resolution_seconds": 10,
                "warning_seconds": 1,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/sla/policies",
            headers={"Authorization": f"Bearer {supervisor_token}"},
            json={
                "priority": "BAJA",
                "first_response_seconds": 0,
                "resolution_seconds": 10,
                "warning_seconds": 1,
            },
        ).status_code
        == 422
    )


def test_future_ticket_and_missing_policy_are_rejected(sla_client):
    client, engine = sla_client
    client_token = login(client, "cliente@demo.com")
    category = category_id(engine)

    from fastapi import HTTPException

    from app.modules.tickets.services.sla_service import SlaService

    with Session(engine) as session:
        future_ticket = Ticket(
            tracking_code="TCK-FUTURE-SLA",
            client_id="future-client",
            category_id=category,
            subject="Ticket futuro",
            description="No debe iniciar el SLA antes de su creación.",
            priority=TicketPriority.MEDIA,
            source=TicketSource.MANUAL,
            created_at=datetime.now(UTC) + timedelta(hours=1),
        )
        with pytest.raises(HTTPException) as error:
            SlaService().start_for_ticket(session, future_ticket)
        assert error.value.status_code == 422
        for policy in session.exec(select(SlaPolicy)).all():
            session.delete(policy)
        session.commit()

    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {client_token}"},
        json={
            "category_id": category,
            "subject": "Sin política SLA",
            "description": "La creación debe informar que falta una política.",
            "priority": "MEDIA",
        },
    )
    assert response.status_code == 422
