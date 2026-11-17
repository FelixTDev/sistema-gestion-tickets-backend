from collections.abc import Iterator
from importlib import import_module, util
from types import SimpleNamespace

import pytest
from pwdlib import PasswordHash
from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import AuditLog, Role, User, UserPreference


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as database_session:
            yield database_session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _advisor_module():
    spec = util.find_spec("app.seed.production_advisor")
    assert spec is not None, "Falta el comando seguro para crear asesores"
    return import_module("app.seed.production_advisor")


def _create_advisor(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    email: str = "Asesor.Demo+uno@gmail.com",
    full_name: str = "Asesor Demo Uno",
    password: str = "AdvisorDemo123",
    app_env: str = "production",
) -> User:
    module = _advisor_module()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(app_env=app_env),
    )
    return module.create_production_advisor(
        session,
        email=email,
        full_name=full_name,
        password=password,
        email_confirmed=True,
    )


def test_creates_only_an_advisor_with_a_hashed_password_and_audit(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisor_role = Role(name="ASESOR", description="Atiende tickets asignados")
    session.add(advisor_role)
    session.commit()

    advisor = _create_advisor(session, monkeypatch)

    assert advisor.email == "asesor.demo+uno@gmail.com"
    assert advisor.full_name == "Asesor Demo Uno"
    assert advisor.role_id == advisor_role.id
    assert advisor.is_active is True
    assert advisor.email_verified is True
    assert advisor.password_hash != "AdvisorDemo123"
    assert PasswordHash.recommended().verify("AdvisorDemo123", advisor.password_hash)
    assert session.get(UserPreference, advisor.id) is not None
    assert session.exec(select(func.count()).select_from(User)).one() == 1

    audit = session.exec(select(AuditLog)).one()
    assert audit.event_type == "SECURITY"
    assert audit.action == "ADVISOR_ACCOUNT_CREATED"
    assert audit.target_user_id == advisor.id
    assert audit.success is True
    assert "AdvisorDemo123" not in str(audit.metadata_json)


def test_refuses_duplicate_email_without_overwriting_the_existing_account(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(Role(name="ASESOR", description="Atiende tickets asignados"))
    session.commit()
    original = _create_advisor(session, monkeypatch)
    original_hash = original.password_hash

    with pytest.raises(RuntimeError, match="ya existe"):
        _create_advisor(
            session,
            monkeypatch,
            email="ASESOR.DEMO+UNO@gmail.com",
            password="DifferentPass456",
        )

    assert session.exec(select(func.count()).select_from(User)).one() == 1
    assert session.get(User, original.id).password_hash == original_hash


def test_refuses_to_run_outside_production(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="APP_ENV=production"):
        _create_advisor(session, monkeypatch, app_env="development")

    assert session.exec(select(func.count()).select_from(User)).one() == 0


def test_requires_the_existing_advisor_role(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="rol ASESOR"):
        _create_advisor(session, monkeypatch)

    assert session.exec(select(func.count()).select_from(User)).one() == 0


def test_requires_confirmation_that_the_email_is_controlled(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _advisor_module()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(app_env="production"),
    )

    with pytest.raises(RuntimeError, match="acceso al correo"):
        module.create_production_advisor(
            session,
            email="advisor@example.com",
            full_name="Asesor Demo",
            password="AdvisorDemo123",
            email_confirmed=False,
        )

    assert session.exec(select(func.count()).select_from(User)).one() == 0


def test_rejects_passwords_that_do_not_meet_the_registration_policy(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(Role(name="ASESOR", description="Atiende tickets asignados"))
    session.commit()

    with pytest.raises(ValueError):
        _create_advisor(session, monkeypatch, password="weak")

    assert session.exec(select(func.count()).select_from(User)).one() == 0
