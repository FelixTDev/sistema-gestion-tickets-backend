from collections.abc import Iterator

import pytest
from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import get_settings
from app.db.models import (
    FAQ,
    Conversation,
    Role,
    SlaPolicy,
    Ticket,
    TicketCategory,
    TicketComment,
    User,
)
from app.seed.production_bootstrap import bootstrap_production_data


@pytest.fixture
def production_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("SECRET_KEY", "test-secret-for-production-bootstrap-32")
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "support@example.test")
    get_settings.cache_clear()
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


def test_bootstrap_creates_only_minimum_production_data(
    production_session: Session,
) -> None:
    email, faq_count = bootstrap_production_data(
        production_session,
        full_name="Supervisor Académico",
        email="SUPERVISOR@example.test",
        password="SecurePass123",
    )

    assert email == "supervisor@example.test"
    assert faq_count > 0
    assert production_session.exec(select(Role)).all()
    assert production_session.exec(select(TicketCategory)).all()
    assert production_session.exec(select(SlaPolicy)).all()
    assert production_session.exec(select(FAQ)).all()
    users = production_session.exec(select(User)).all()
    assert len(users) == 1
    assert users[0].email == email
    assert production_session.exec(select(Ticket)).all() == []
    assert production_session.exec(select(Conversation)).all() == []
    assert production_session.exec(select(TicketComment)).all() == []


def test_bootstrap_refuses_to_overwrite_an_existing_account(
    production_session: Session,
) -> None:
    bootstrap_production_data(
        production_session,
        full_name="Supervisor Académico",
        email="supervisor@example.test",
        password="SecurePass123",
    )

    with pytest.raises(RuntimeError, match="ya existe"):
        bootstrap_production_data(
            production_session,
            full_name="Otro Supervisor",
            email="supervisor@example.test",
            password="DifferentPass456",
        )

    assert production_session.exec(select(func.count()).select_from(User)).one() == 1
