from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.db.session import get_session
from app.main import app
from app.modules.usuarios.models.role import Role
from app.modules.usuarios.models.user import User
from app.seed.demo_data import seed_demo_data


@pytest.fixture
def advisor_client() -> Generator[tuple[TestClient, object], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session)

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


def add_advisor(
    engine: object, *, full_name: str, email: str, is_active: bool = True
) -> User:
    with Session(engine) as session:
        role = session.exec(select(Role).where(Role.name == "ASESOR")).one()
        advisor = User(
            full_name=full_name,
            email=email,
            password_hash="not-returned",
            role_id=role.id,
            is_active=is_active,
        )
        session.add(advisor)
        session.commit()
        session.refresh(advisor)
        return advisor


def test_supervisor_gets_only_active_advisors_in_stable_name_order(advisor_client):
    client, engine = advisor_client
    add_advisor(
        engine,
        full_name="Zoe Asesora",
        email="zoe.advisor@example.com",
    )
    add_advisor(
        engine,
        full_name="Ana Asesora",
        email="ana.advisor@example.com",
    )
    add_advisor(
        engine,
        full_name="Inactivo Asesor",
        email="inactive.advisor@example.com",
        is_active=False,
    )

    response = client.get(
        "/api/v1/users/advisors",
        headers={"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["full_name"] for item in body] == sorted(
        item["full_name"] for item in body
    )
    assert {item["email"] for item in body} == {
        "ana.advisor@example.com",
        "asesor@demo.com",
        "zoe.advisor@example.com",
    }
    assert all(item["role"] == "ASESOR" for item in body)
    assert all("inactive" not in item["email"] for item in body)
    assert set(body[0]) == {"id", "full_name", "email", "role"}
    assert "password_hash" not in response.text
    assert "access_token" not in response.text


def test_directory_excludes_clients_and_supervisors(advisor_client):
    client, _ = advisor_client

    response = client.get(
        "/api/v1/users/advisors",
        headers={"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"},
    )

    assert response.status_code == 200
    emails = {item["email"] for item in response.json()}
    assert "cliente@demo.com" not in emails
    assert "supervisor@demo.com" not in emails


@pytest.mark.parametrize("email", ["cliente@demo.com", "asesor@demo.com"])
def test_only_supervisor_can_access_directory(advisor_client, email: str):
    client, _ = advisor_client

    response = client.get(
        "/api/v1/users/advisors",
        headers={"Authorization": f"Bearer {login(client, email)}"},
    )

    assert response.status_code == 403


def test_directory_requires_authentication(advisor_client):
    client, _ = advisor_client

    response = client.get("/api/v1/users/advisors")

    assert response.status_code == 401


def test_directory_returns_empty_list_when_no_active_advisor(advisor_client):
    client, engine = advisor_client
    with Session(engine) as session:
        advisor = session.exec(
            select(User).where(User.email == "asesor@demo.com")
        ).one()
        advisor.is_active = False
        session.add(advisor)
        session.commit()

    response = client.get(
        "/api/v1/users/advisors",
        headers={"Authorization": f"Bearer {login(client, 'supervisor@demo.com')}"},
    )

    assert response.status_code == 200
    assert response.json() == []
