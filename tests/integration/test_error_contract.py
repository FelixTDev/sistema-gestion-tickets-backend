from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import get_settings
from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.conocimiento.models.category import TicketCategory
from app.seed.demo_data import seed_demo_data

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
ERROR_KEYS = {"code", "message", "details", "request_id"}


@pytest.fixture
def error_client() -> Generator[tuple[TestClient, object], None, None]:
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


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "demo-password-local"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _category_id(engine: object) -> str:
    with Session(engine) as session:
        category = session.exec(select(TicketCategory)).first()
        assert category is not None
        return category.id


def _create_ticket(client: TestClient, engine: object, token: str) -> dict:
    response = client.post(
        "/api/v1/tickets",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "category_id": _category_id(engine),
            "subject": "Contrato de errores",
            "description": "Ticket para validar errores HTTP normalizados.",
            "priority": "MEDIA",
        },
    )
    assert response.status_code == 201
    return response.json()


def _assert_error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert set(response.json()) == ERROR_KEYS
    assert response.json()["code"] == code
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_success_and_errors_always_return_a_strict_request_id(error_client):
    client, _ = error_client
    valid_request_id = "frontend-2026.10:request_1"

    success = client.get("/api/v1/health", headers={"X-Request-ID": valid_request_id})
    unauthorized = client.get(
        "/api/v1/auth/me", headers={"X-Request-ID": valid_request_id}
    )

    assert success.headers["X-Request-ID"] == valid_request_id
    assert unauthorized.headers["X-Request-ID"] == valid_request_id
    assert unauthorized.json()["request_id"] == valid_request_id


@pytest.mark.parametrize(
    "invalid_request_id",
    [" leading-space", "contains/slash", "x" * 65, "starts@bad"],
)
def test_invalid_incoming_request_id_is_replaced(error_client, invalid_request_id: str):
    client, _ = error_client

    response = client.get(
        "/api/v1/health", headers={"X-Request-ID": invalid_request_id}
    )

    generated = response.headers["X-Request-ID"]
    assert response.status_code == 200
    assert generated != invalid_request_id
    assert len(generated) <= 64


def test_http_error_statuses_use_normalized_safe_envelopes(error_client):
    client, engine = error_client
    client_token = _login(client, "cliente@demo.com")
    supervisor_token = _login(client, "supervisor@demo.com")
    ticket = _create_ticket(client, engine, client_token)

    bad_request = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "invalid-token", "new_password": "Nueva-1234"},
    )
    unauthorized = client.get("/api/v1/auth/me")
    forbidden = client.get(
        "/api/v1/reports/summary",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    not_found = client.get(
        "/api/v1/tickets/not-found",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )
    conflict = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Cliente duplicado",
            "email": "cliente@demo.com",
            "password": "Segura-1234",
        },
    )
    validation = client.post("/api/v1/auth/login", json={"password": "secret"})

    settings = get_settings()
    original_max_size = settings.attachment_max_file_size_bytes
    settings.attachment_max_file_size_bytes = len(PDF_BYTES) - 1
    try:
        too_large = client.post(
            f"/api/v1/tickets/{ticket['id']}/attachments",
            headers={"Authorization": f"Bearer {client_token}"},
            files={"file": ("large.pdf", PDF_BYTES, "application/pdf")},
        )
    finally:
        settings.attachment_max_file_size_bytes = original_max_size

    unsupported = client.post(
        f"/api/v1/tickets/{ticket['id']}/attachments",
        headers={"Authorization": f"Bearer {client_token}"},
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )

    original_limit = settings.chatbot_max_anonymous_conversations_per_hour
    settings.chatbot_max_anonymous_conversations_per_hour = 1
    try:
        first_conversation = client.post("/api/v1/chat/conversations")
        rate_limited = client.post("/api/v1/chat/conversations")
    finally:
        settings.chatbot_max_anonymous_conversations_per_hour = original_limit
    assert first_conversation.status_code == 201

    expected = (
        (bad_request, 400, "BAD_REQUEST"),
        (unauthorized, 401, "AUTHENTICATION_REQUIRED"),
        (forbidden, 403, "FORBIDDEN"),
        (not_found, 404, "NOT_FOUND"),
        (conflict, 409, "CONFLICT"),
        (too_large, 413, "PAYLOAD_TOO_LARGE"),
        (unsupported, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (validation, 422, "VALIDATION_ERROR"),
        (rate_limited, 429, "RATE_LIMITED"),
    )
    for response, status_code, code in expected:
        _assert_error(response, status_code, code)

    assert unauthorized.headers["WWW-Authenticate"] == "Bearer"
    validation_body = validation.json()
    assert validation_body["details"]
    assert all(
        set(detail) == {"loc", "msg", "type"} for detail in validation_body["details"]
    )
    assert "secret" not in validation.text


def test_unhandled_exception_is_sanitized_without_sql_trace_or_secret(error_client):
    _, _engine = error_client
    route_path = "/_test/unhandled-error-contract"

    def raise_sensitive_error() -> None:
        raise RuntimeError(
            "mysql+pymysql://user:super-secret@db/tickets SELECT * FROM users"
        )

    app.add_api_route(route_path, raise_sensitive_error, methods=["GET"])
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                route_path,
                headers={
                    "Origin": "http://localhost:5173",
                    "X-Request-ID": "safe-request-id",
                },
            )
    finally:
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if getattr(route, "path", None) != route_path
        ]

    _assert_error(response, 500, "INTERNAL_SERVER_ERROR")
    assert response.headers["X-Request-ID"] == "safe-request-id"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert response.headers["Access-Control-Expose-Headers"] == "X-Request-ID"
    body = response.text.casefold()
    assert "super-secret" not in body
    assert "mysql+pymysql" not in body
    assert "select *" not in body
    assert "traceback" not in body


def test_ticket_domain_violations_have_stable_error_codes(error_client):
    client, engine = error_client
    client_token = _login(client, "cliente@demo.com")
    supervisor_token = _login(client, "supervisor@demo.com")
    advisor_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "asesor@demo.com",
            "password": "demo-password-local",
        },
    )
    advisor_id = advisor_login.json()["user"]["id"]

    unassigned = _create_ticket(client, engine, client_token)
    assignment_required = client.post(
        f"/api/v1/tickets/{unassigned['id']}/status",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"status": "ASIGNADO"},
    )

    assigned = _create_ticket(client, engine, client_token)
    assigned_response = client.post(
        f"/api/v1/tickets/{assigned['id']}/assignments",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"advisor_id": advisor_id},
    )
    assert assigned_response.status_code == 201
    release_reason_required = client.post(
        f"/api/v1/tickets/{assigned['id']}/release",
        headers={"Authorization": f"Bearer {supervisor_token}"},
    )

    terminal = _create_ticket(client, engine, client_token)
    cancelled = client.post(
        f"/api/v1/tickets/{terminal['id']}/cancel",
        headers={"Authorization": f"Bearer {supervisor_token}"},
        json={"reason": "Cierre defensivo del caso"},
    )
    assert cancelled.status_code == 200
    terminal_mutation = client.post(
        f"/api/v1/tickets/{terminal['id']}/attachments",
        headers={"Authorization": f"Bearer {client_token}"},
        files={"file": ("evidence.pdf", PDF_BYTES, "application/pdf")},
    )

    _assert_error(
        assignment_required,
        409,
        "TICKET_ASSIGNMENT_REQUIRED",
    )
    _assert_error(
        release_reason_required,
        422,
        "TICKET_RELEASE_REASON_REQUIRED",
    )
    _assert_error(
        terminal_mutation,
        409,
        "TICKET_TERMINAL_MUTATION_FORBIDDEN",
    )


def test_openapi_documents_errors_streams_and_optional_auth():
    app.openapi_schema = None
    schema = app.openapi()

    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))

    documented_errors = (
        ("/api/v1/tickets/{ticket_id}/release", "post", "409"),
        ("/api/v1/tickets/{ticket_id}/release", "post", "422"),
        ("/api/v1/tickets/{ticket_id}/attachments", "post", "413"),
        ("/api/v1/tickets/{ticket_id}/attachments", "post", "415"),
        ("/api/v1/chat/conversations", "post", "429"),
        ("/api/v1/faqs/{faq_id}/feedback", "post", "404"),
        ("/api/v1/reports/summary", "get", "403"),
    )
    for path, method, status_code in documented_errors:
        response = schema["paths"][path][method]["responses"][status_code]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }

    download_content = schema["paths"]["/api/v1/attachments/{attachment_id}/download"][
        "get"
    ]["responses"]["200"]["content"]
    export_content = schema["paths"]["/api/v1/reports/{report_name}/export"]["get"][
        "responses"
    ]["200"]["content"]
    assert download_content == {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
    }
    assert export_content == {
        "text/csv": {"schema": {"type": "string", "format": "binary"}}
    }

    optional_auth_operations = (
        ("/api/v1/faqs/{faq_id}/feedback", "post"),
        ("/api/v1/chat/conversations", "post"),
        ("/api/v1/chat/conversations/{conversation_id}", "get"),
        ("/api/v1/chat/conversations/{conversation_id}/messages", "post"),
        ("/api/v1/chat/conversations/{conversation_id}/escalate", "post"),
        ("/api/v1/chat/conversations/{conversation_id}/reset", "post"),
        ("/api/v1/chat/conversations/{conversation_id}/feedback", "post"),
    )
    for path, method in optional_auth_operations:
        assert schema["paths"][path][method]["security"] == [
            {},
            {"HTTPBearer": []},
        ]

    assert "patch" not in schema["paths"]["/api/v1/tickets/{ticket_id}"]
