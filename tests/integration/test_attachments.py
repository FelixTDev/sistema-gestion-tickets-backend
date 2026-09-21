from collections.abc import Generator
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.adjuntos.api.router import get_attachment_service
from app.modules.adjuntos.services.attachment_service import AttachmentService
from app.modules.adjuntos.services.storage_provider import LocalStorageProvider
from app.modules.conocimiento.models.category import TicketCategory
from app.seed.demo_data import seed_demo_data

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture
def attachment_client(tmp_path) -> Generator[tuple[TestClient, object], None, None]:
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

    def override_attachment_service() -> AttachmentService:
        return AttachmentService(
            storage_provider=LocalStorageProvider(tmp_path),
            max_file_size=1024,
            max_total_size_per_ticket=1500,
            max_files_per_ticket=2,
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_attachment_service] = override_attachment_service
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


def create_ticket(client: TestClient, token: str, category: str) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": category,
            "subject": "Ticket con adjunto",
            "description": "Descripción para validar adjuntos seguros.",
            "priority": "MEDIA",
        },
    )
    assert response.status_code == 201
    return response.json()


def advisor_credentials(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "asesor@demo.com", "password": "demo-password-local"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["access_token"], body["user"]["id"]


def upload(
    client: TestClient,
    ticket_id: str,
    token: str,
    filename: str = "documento.pdf",
    content: bytes = PDF_BYTES,
    content_type: str = "application/pdf",
    comment_id: str | None = None,
):
    data = {"comment_id": comment_id} if comment_id is not None else None
    return client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, content_type)},
        data=data,
    )


def assign_ticket(
    client: TestClient, ticket_id: str, supervisor_token: str, advisor_id: str
) -> None:
    response = client.post(
        f"/api/v1/tickets/{ticket_id}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert response.status_code == 201


def test_upload_list_download_and_delete_attachment(attachment_client):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))
    comment = client.post(
        f"/api/v1/tickets/{ticket['id']}/comments",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "Comentario que tendrá un documento."},
    )
    assert comment.status_code == 201

    uploaded = upload(client, ticket["id"], token, comment_id=comment.json()["id"])
    body = uploaded.json()

    assert uploaded.status_code == 201
    assert body["ticket_id"] == ticket["id"]
    assert body["comment_id"] == comment.json()["id"]
    assert body["original_filename"] == "documento.pdf"
    assert body["mime_type_declared"] == "application/pdf"
    assert body["mime_type_detected"] == "application/pdf"
    assert body["file_size"] == len(PDF_BYTES)
    assert body["sha256"] == sha256(PDF_BYTES).hexdigest()
    assert body["status"] == "ACTIVE"
    assert "storage_key" not in body
    assert "password_hash" not in str(body)

    listed = client.get(
        f"/api/v1/tickets/{ticket['id']}/attachments",
        headers={"Authorization": f"Bearer {token}"},
    )
    downloaded = client.get(
        f"/api/v1/attachments/{body['id']}/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    deleted = client.delete(
        f"/api/v1/attachments/{body['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    after_delete = client.get(
        f"/api/v1/attachments/{body['id']}/download",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]
    assert downloaded.status_code == 200
    assert downloaded.content == PDF_BYTES
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "no-store"
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "DELETED"
    assert after_delete.status_code == 404


def test_permissions_isolate_client_and_allow_assigned_advisor_and_supervisor(
    attachment_client,
):
    client, engine = attachment_client
    owner_token = login(client, "cliente@demo.com")
    supervisor_token = login(client, "supervisor@demo.com")
    advisor_token, advisor_id = advisor_credentials(client)
    ticket = create_ticket(client, owner_token, category_id(engine))
    attachment = upload(client, ticket["id"], owner_token).json()

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cliente externo",
            "email": "attachment.other@example.com",
            "password": "Demo-password-123",
        },
    )
    assert registered.status_code == 201
    other_client_token = login(
        client, "attachment.other@example.com", "Demo-password-123"
    )

    assign_ticket(client, ticket["id"], supervisor_token, advisor_id)
    other_list = client.get(
        f"/api/v1/tickets/{ticket['id']}/attachments",
        headers={"Authorization": f"Bearer {other_client_token}"},
    )
    advisor_list = client.get(
        f"/api/v1/tickets/{ticket['id']}/attachments",
        headers={"Authorization": f"Bearer {advisor_token}"},
    )
    supervisor_download = client.get(
        f"/api/v1/attachments/{attachment['id']}/download",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    assert other_list.status_code == 403
    assert advisor_list.status_code == 200
    assert supervisor_download.status_code == 200


def test_attachment_http_errors_and_missing_resources(attachment_client):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))

    unauthenticated = client.get(f"/api/v1/tickets/{ticket['id']}/attachments")
    missing_ticket = client.get(
        "/api/v1/tickets/missing-ticket/attachments",
        headers={"Authorization": f"Bearer {token}"},
    )
    missing_attachment = client.get(
        "/api/v1/attachments/missing-attachment/download",
        headers={"Authorization": f"Bearer {token}"},
    )
    invalid_payload = client.post(
        f"/api/v1/tickets/{ticket['id']}/attachments",
        headers={"Authorization": f"Bearer {token}"},
        data={"comment_id": "not-a-comment"},
    )

    assert unauthenticated.status_code == 401
    assert missing_ticket.status_code == 404
    assert missing_attachment.status_code == 404
    assert invalid_payload.status_code == 422


def test_attachment_validation_rejects_size_mime_content_and_empty_files(
    attachment_client,
):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))

    too_large = upload(client, ticket["id"], token, content=PDF_BYTES + b"x" * 1024)
    blocked_extension = upload(
        client,
        ticket["id"],
        token,
        filename="program.exe",
        content=b"MZ executable",
        content_type="application/octet-stream",
    )
    wrong_declared_mime = upload(
        client,
        ticket["id"],
        token,
        filename="documento.pdf",
        content_type="image/png",
    )
    corrupt_content = upload(
        client,
        ticket["id"],
        token,
        filename="documento.pdf",
        content=b"not a pdf",
    )
    empty = upload(client, ticket["id"], token, content=b"")

    assert too_large.status_code == 413
    assert blocked_extension.status_code == 415
    assert wrong_declared_mime.status_code == 415
    assert corrupt_content.status_code == 415
    assert empty.status_code == 422


def test_attachment_names_are_sanitized_and_path_traversal_is_rejected(
    attachment_client,
):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))

    traversal = upload(client, ticket["id"], token, filename="../outside.pdf")
    dangerous_name = upload(client, ticket["id"], token, filename="factura<>.pdf")

    assert traversal.status_code == 422
    assert dangerous_name.status_code == 201
    assert "<" not in dangerous_name.json()["original_filename"]
    assert ">" not in dangerous_name.json()["original_filename"]


def test_attachment_count_and_total_ticket_quotas(attachment_client):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))
    first_content = PDF_BYTES + b"a" * 850
    second_content = PDF_BYTES + b"b" * 650

    first = upload(client, ticket["id"], token, content=first_content)
    exceeds_total = upload(client, ticket["id"], token, content=second_content)
    second = upload(
        client, ticket["id"], token, filename="second.pdf", content=PDF_BYTES
    )
    third = upload(client, ticket["id"], token, filename="third.pdf", content=PDF_BYTES)

    assert first.status_code == 201
    assert exceeds_total.status_code == 413
    assert second.status_code == 201
    assert third.status_code == 413


def test_attachment_history_records_actor_without_binary(attachment_client):
    client, engine = attachment_client
    token = login(client, "cliente@demo.com")
    ticket = create_ticket(client, token, category_id(engine))
    uploaded = upload(client, ticket["id"], token)
    attachment_id = uploaded.json()["id"]
    client.delete(
        f"/api/v1/attachments/{attachment_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    from app.modules.tickets.models.history import TicketHistory

    with Session(engine) as session:
        history = session.exec(
            select(TicketHistory).where(
                TicketHistory.ticket_id == ticket["id"],
                TicketHistory.action.in_(["ATTACHMENT_UPLOADED", "ATTACHMENT_DELETED"]),
            )
        ).all()

    assert {item.action for item in history} == {
        "ATTACHMENT_UPLOADED",
        "ATTACHMENT_DELETED",
    }
    assert all(item.actor_id is not None for item in history)
    assert all("%PDF" not in (item.description or "") for item in history)
