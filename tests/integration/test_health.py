from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.core.config import Settings
from app.db.session import get_session
from app.main import app


@pytest.fixture
def health_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_health_endpoint_returns_api_status(health_client: TestClient):
    response = health_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ticket-management-api"}


def test_readiness_returns_200_after_minimal_database_query(
    health_client: TestClient,
):
    response = health_client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "ticket-management-api",
        "database": "ok",
    }


def test_readiness_returns_safe_503_when_database_is_unavailable(
    health_client: TestClient,
):
    class UnavailableSession:
        def exec(self, _statement):
            raise OperationalError(
                "SELECT secret FROM users",
                {"password": "database-secret"},
                RuntimeError("mysql+pymysql://user:password@db/tickets"),
            )

    def unavailable_session():
        yield UnavailableSession()

    app.dependency_overrides[get_session] = unavailable_session
    response = health_client.get(
        "/api/v1/health/ready",
        headers={"X-Request-ID": "readiness-test"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DATABASE_NOT_READY",
        "message": "La aplicación no está lista.",
        "details": None,
        "request_id": "readiness-test",
    }
    assert response.headers["X-Request-ID"] == "readiness-test"
    body = response.text.casefold()
    assert "select" not in body
    assert "database-secret" not in body
    assert "mysql+pymysql" not in body
    assert "traceback" not in body


def test_database_timeout_is_validated_and_only_applied_to_mysql():
    from app.db import session as db_session

    mysql_settings = Settings(
        app_env="test",
        database_url="mysql+pymysql://user:password@db/tickets",
        database_connect_timeout_seconds=7,
    )
    sqlite_settings = Settings(
        app_env="test",
        database_url="sqlite://",
        database_connect_timeout_seconds=7,
    )

    assert db_session.database_connect_args(mysql_settings) == {
        "connect_timeout": 7,
        "read_timeout": 7,
        "write_timeout": 7,
    }
    assert db_session.database_connect_args(sqlite_settings) == {}
    with pytest.raises(ValueError):
        Settings(app_env="test", database_connect_timeout_seconds=0)


def test_readiness_openapi_documents_success_and_safe_failure():
    app.openapi_schema = None
    operation = app.openapi()["paths"]["/api/v1/health/ready"]["get"]

    assert set(operation["responses"]) >= {"200", "503"}
    assert operation["responses"]["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
