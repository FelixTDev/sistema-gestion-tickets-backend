from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def auth_client() -> Generator[TestClient, None, None]:
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_registers_client_without_exposing_hash(auth_client: TestClient):
    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Nuevo Cliente",
            "email": "nuevo@example.com",
            "password": "Seguro-1234",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "nuevo@example.com"
    assert body["role"] == "CLIENTE"
    assert "password_hash" not in body
    assert "access_token" not in body


def test_rejects_duplicate_email(auth_client: TestClient):
    payload = {
        "full_name": "Duplicado",
        "email": "cliente@demo.com",
        "password": "Seguro-1234",
    }

    response = auth_client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"] == "El correo electrónico ya está registrado"


@pytest.mark.parametrize(
    "password", ["short", "sin-mayuscula-123", "SIN-MINUSCULA-123", "SinNumero"]
)
def test_rejects_invalid_password(auth_client: TestClient, password: str):
    response = auth_client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Inválido",
            "email": "invalid@example.com",
            "password": password,
        },
    )

    assert response.status_code == 422


def test_login_returns_jwt_and_me_returns_public_user(auth_client: TestClient):
    response = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@demo.com", "password": "demo-password-local"},
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert me.status_code == 200
    assert me.json()["email"] == "cliente@demo.com"
    assert me.json()["role"] == "CLIENTE"
    assert "password_hash" not in me.json()


def test_rejects_invalid_credentials_and_missing_or_invalid_token(
    auth_client: TestClient,
):
    wrong_password = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@demo.com", "password": "incorrecta"},
    )
    missing_token = auth_client.get("/api/v1/auth/me")
    invalid_token = auth_client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.value"}
    )

    assert wrong_password.status_code == 401
    assert missing_token.status_code == 401
    assert invalid_token.status_code == 401


def test_logout_requires_authentication_and_returns_confirmation(
    auth_client: TestClient,
):
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"email": "cliente@demo.com", "password": "demo-password-local"},
    )
    response = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Sesión cerrada correctamente"}


@pytest.mark.parametrize(
    ("email", "role"),
    [
        ("cliente@demo.com", "CLIENTE"),
        ("asesor@demo.com", "ASESOR"),
        ("supervisor@demo.com", "SUPERVISOR"),
    ],
)
def test_each_demo_account_can_login_and_read_own_role(
    auth_client: TestClient, email: str, role: str
):
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "demo-password-local"},
    )

    assert login.status_code == 200
    me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["role"] == role


def test_seed_roles_are_rejected_by_role_dependency_when_not_allowed():
    from fastapi import HTTPException

    from app.api.deps import AuthenticatedUser, require_roles
    from app.modules.usuarios.models.user import User

    client_user = AuthenticatedUser(
        user=User(
            role_id="client-role",
            full_name="Cliente",
            email="c@e.com",
            password_hash="hash",
        ),
        role="CLIENTE",
    )
    checker = require_roles("SUPERVISOR")

    with pytest.raises(HTTPException) as error:
        checker(client_user)

    assert error.value.status_code == 403
