from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import AuthenticatedUser
from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.adjuntos.services.attachment_service import AttachmentService
from app.modules.auditoria.models.audit_log import AuditLog
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.auditoria.services.redaction import redact_data
from app.modules.conocimiento.models.category import TicketCategory
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def audit_client() -> Generator[tuple[TestClient, object], None, None]:
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
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_redaction_is_recursive_deterministic_and_does_not_keep_binary() -> None:
    payload = {
        "Password": "do-not-store",
        "nested": {"access_token": "secret-token", "safe": "value"},
        "blob": b"binary-data",
        "long_message": "x" * 3000,
        "items": [{"password_hash": "hash"}],
    }

    first = redact_data(payload)
    second = redact_data(payload)

    assert first == second
    assert first["Password"] == "[REDACTED]"
    assert first["nested"]["access_token"] == "[REDACTED]"
    assert first["blob"] == "[BINARY_REDACTED]"
    assert first["items"][0]["password_hash"] == "[REDACTED]"
    assert "x" * 3000 not in str(first)


def test_audit_service_is_append_only_and_deduplicates(audit_client) -> None:
    _, engine = audit_client
    with Session(engine) as session:
        service = AuditService()
        first = service.record(
            session,
            event_type="SECURITY",
            action="LOGIN_FAILED",
            actor_user_id=None,
            actor_role=None,
            resource_type="AUTHENTICATION",
            resource_id=None,
            success=False,
            error_code="INVALID_CREDENTIALS",
            metadata={"password": "never-store", "email": "private@example.com"},
            dedupe_key="test-login-failure-1",
        )
        second = service.record(
            session,
            event_type="SECURITY",
            action="LOGIN_FAILED",
            actor_user_id=None,
            actor_role=None,
            resource_type="AUTHENTICATION",
            resource_id=None,
            success=False,
            error_code="INVALID_CREDENTIALS",
            metadata={"password": "different"},
            dedupe_key="test-login-failure-1",
        )
        session.commit()

        assert first is not None
        assert second is first
        rows = session.exec(select(AuditLog)).all()
        assert len(rows) == 1
        assert rows[0].metadata_json["password"] == "[REDACTED]"
        assert "private@example.com" not in str(rows[0].metadata_json)


def test_successful_login_and_logout_are_audited_without_tokens(audit_client) -> None:
    client, engine = audit_client
    token = login(client, "supervisor@demo.com")
    response = client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    with Session(engine) as session:
        actions = session.exec(select(AuditLog.action)).all()
        assert "LOGIN_SUCCESS" in actions
        assert "LOGOUT" in actions
        serialized = str(session.exec(select(AuditLog)).all())
        assert token not in serialized
        assert "password_hash" not in serialized


def test_failed_login_is_audited_without_credentials(audit_client) -> None:
    client, engine = audit_client
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401

    with Session(engine) as session:
        row = session.exec(
            select(AuditLog).where(AuditLog.action == "LOGIN_FAILED")
        ).one()
        assert row.success is False
        assert "missing@example.com" not in str(row.metadata_json)
        assert "wrong-password" not in str(row.metadata_json)


def test_password_recovery_requests_are_audited_without_email_or_token(
    audit_client,
) -> None:
    client, engine = audit_client
    for email in ("cliente@demo.com", "missing@example.com"):
        response = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": email},
        )
        assert response.status_code == 202
    with Session(engine) as session:
        rows = session.exec(
            select(AuditLog).where(AuditLog.action == "PASSWORD_RESET_REQUESTED")
        ).all()
        assert len(rows) == 2
        serialized = str(rows)
        assert "cliente@demo.com" not in serialized
        assert "missing@example.com" not in serialized
        assert "token" not in serialized.casefold()


def test_audit_query_is_supervisor_only_paginated_and_stable(audit_client) -> None:
    client, engine = audit_client
    with Session(engine) as session:
        service = AuditService()
        for index in range(3):
            service.record(
                session,
                event_type="TICKET",
                action="UPDATED",
                actor_user_id=None,
                actor_role="SUPERVISOR",
                resource_type="TICKET",
                resource_id=f"ticket-{index}",
                success=True,
                occurred_at=datetime.now(UTC) - timedelta(seconds=index),
            )
        session.commit()

    response = client.get(
        "/api/v1/audit",
        params={"page": 1, "page_size": 2, "event_type": "TICKET"},
        headers={"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["total"] == 3
    assert body["total_pages"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["resource_id"] == "ticket-0"
    assert "password_hash" not in response.text


@pytest.mark.parametrize("email", ["cliente@demo.com", "asesor@demo.com"])
def test_non_supervisors_cannot_query_global_audit(audit_client, email: str) -> None:
    client, _ = audit_client
    response = client.get(
        "/api/v1/audit",
        headers={"Authorization": f"Bearer {login(client, email)}"},
    )
    assert response.status_code == 403


def test_unauthenticated_cannot_query_global_audit(audit_client) -> None:
    client, _ = audit_client
    assert client.get("/api/v1/audit").status_code == 401


def test_audit_query_rejects_invalid_pagination(audit_client) -> None:
    client, _ = audit_client
    response = client.get(
        "/api/v1/audit?page=0&page_size=0",
        headers={"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"},
    )
    assert response.status_code == 422


def test_global_audit_has_no_mutation_endpoints(audit_client) -> None:
    client, engine = audit_client
    with Session(engine) as session:
        row = AuditService().record(
            session,
            event_type="SECURITY",
            action="SECURITY_EVENT",
            actor_user_id=None,
            actor_role=None,
            resource_type="AUTHENTICATION",
            resource_id=None,
            success=True,
        )
        row_id = row.id
        session.commit()

    token = login(client, "supervisor@demo.com")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.patch(
        f"/api/v1/audit/{row_id}", headers=headers, json={}
    ).status_code in {404, 405}
    assert client.delete(f"/api/v1/audit/{row_id}", headers=headers).status_code in {
        404,
        405,
    }


def test_ticket_assignment_comment_and_profile_write_global_events(
    audit_client,
) -> None:
    client, engine = audit_client
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    client_token = login(client, "cliente@demo.com")
    ticket_response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {client_token}"},
        json={
            "category_id": category.id,
            "subject": "Auditoría transversal",
            "description": "Verificar trazabilidad del ticket",
            "priority": "MEDIA",
        },
    )
    assert ticket_response.status_code == 201, ticket_response.text
    ticket_id = ticket_response.json()["id"]

    supervisor_token = login(client, "supervisor@demo.com")
    with Session(engine) as session:
        advisor = session.exec(
            select(models.User).where(models.User.email == "asesor@demo.com")
        ).one()
    assignment = client.post(
        f"/api/v1/tickets/{ticket_id}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor.id},
    )
    assert assignment.status_code == 201, assignment.text
    advisor_token = login(client, "asesor@demo.com")
    comment = client.post(
        f"/api/v1/tickets/{ticket_id}/comments",
        headers={"Authorization": f"Bearer {advisor_token}"},
        json={"content": "Respuesta auditada"},
    )
    assert comment.status_code == 201, comment.text

    profile = client.patch(
        "/api/v1/users/me/profile",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"full_name": "Cliente Auditado"},
    )
    assert profile.status_code == 200, profile.text
    preferences = client.patch(
        "/api/v1/users/me/preferences",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"comment_enabled": False},
    )
    assert preferences.status_code == 200, preferences.text

    with Session(engine) as session:
        actions = set(
            session.exec(
                select(AuditLog.action).where(AuditLog.resource_id == ticket_id)
            ).all()
        )
        assert {"CREATED", "SLA_STARTED", "ASSIGNED", "COMMENT_ADDED"} <= actions
        user_actions = set(
            session.exec(
                select(AuditLog.action).where(
                    AuditLog.resource_type.in_(["USER", "USER_PREFERENCES"])
                )
            ).all()
        )
        assert {"PROFILE_UPDATED", "PREFERENCES_UPDATED"} <= user_actions


def test_report_and_knowledge_events_are_redacted_and_queryable(audit_client) -> None:
    client, engine = audit_client
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    supervisor_token = login(client, "supervisor@demo.com")
    faq_response = client.post(
        "/api/v1/faqs",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={
            "category_id": category.id,
            "title": "FAQ auditable",
            "question": "¿Cómo se audita?",
            "answer": "Se registra el evento mínimo necesario.",
            "keywords": "auditoria,trazabilidad",
        },
    )
    assert faq_response.status_code == 201, faq_response.text
    export_response = client.get(
        "/api/v1/reports/summary/export",
        params={"format": "csv"},
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    assert export_response.status_code == 200, export_response.text

    with Session(engine) as session:
        rows = session.exec(
            select(AuditLog).where(AuditLog.event_type.in_(["KNOWLEDGE", "REPORT"]))
        ).all()
        assert any(row.action == "CREATED" for row in rows)
        assert any(row.action == "EXPORT_SUCCEEDED" for row in rows)
        serialized = str(rows)
        assert "password_hash" not in serialized
        assert "access_token" not in serialized


def test_attachment_audit_keeps_metadata_without_binary_payload(audit_client) -> None:
    client, engine = audit_client
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
    token = login(client, "cliente@demo.com")
    ticket_response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category.id,
            "subject": "Adjunto auditado",
            "description": "El binario no debe llegar a la auditoría",
        },
    )
    assert ticket_response.status_code == 201
    ticket_id = ticket_response.json()["id"]
    with Session(engine) as session:
        user = session.exec(
            select(models.User).where(models.User.email == "cliente@demo.com")
        ).one()
        actor = AuthenticatedUser(user=user, role="CLIENTE")
        AttachmentService()._history(
            session,
            ticket_id,
            actor,
            "ATTACHMENT_UPLOADED",
            "attachment-id",
        )
        session.commit()
        row = session.exec(
            select(AuditLog).where(AuditLog.event_type == "ATTACHMENT")
        ).one()
        assert row.metadata_json == {"ticket_id": ticket_id}
        assert "binary" not in str(row.metadata_json).casefold()
