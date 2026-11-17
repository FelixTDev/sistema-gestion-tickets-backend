from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.version import FAQVersion
from app.modules.conocimiento.services.faq_content_policy import (
    FAQ_CARD_REQUIREMENTS_ANSWER,
    FAQ_PORTAL_PASSWORD_ANSWER,
    SBS_CLAIMS_REGULATION_URL,
)
from app.modules.tickets.models.ticket import Ticket
from app.seed.demo_data import (
    DEMO_FAQS,
    seed_chatbot_knowledge,
    seed_demo_data,
)


@pytest.fixture
def knowledge_seed_engine() -> Generator[object, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_safe_knowledge_seed_clears_sources_and_does_not_rewrite_tickets(
    knowledge_seed_engine,
) -> None:
    with Session(knowledge_seed_engine) as session:
        seed_demo_data(session)
        tickets_before = {
            ticket.id: (ticket.status, ticket.version, ticket.subject)
            for ticket in session.exec(select(Ticket)).all()
        }
        faq_versions_before = {
            faq.id: faq.version for faq in session.exec(select(FAQ)).all()
        }

        count = seed_chatbot_knowledge(session)
        first_versions = {
            faq.id: faq.version for faq in session.exec(select(FAQ)).all()
        }
        tickets_after = {
            ticket.id: (ticket.status, ticket.version, ticket.subject)
            for ticket in session.exec(select(Ticket)).all()
        }
        published = session.exec(
            select(FAQ).where(FAQ.status == FAQ.Status.PUBLISHED, FAQ.is_active)
        ).all()
        snapshots = session.exec(select(FAQVersion)).all()

        assert count == len(DEMO_FAQS)
        assert len(published) >= len(DEMO_FAQS)
        claim_question = "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
        assert all(
            (
                faq.source_url == SBS_CLAIMS_REGULATION_URL
                and faq.source_verified_at is not None
            )
            if faq.question == claim_question
            else faq.source_url is None and faq.source_verified_at is None
            for faq in published
            if faq.question in {item[1] for item in DEMO_FAQS}
        )
        assert all(
            (
                snapshot.source_url == SBS_CLAIMS_REGULATION_URL
                and snapshot.source_verified_at is not None
            )
            if snapshot.question == claim_question
            else snapshot.source_url is None and snapshot.source_verified_at is None
            for snapshot in snapshots
            if snapshot.faq_id in {faq.id for faq in published}
        )
        assert tickets_after == tickets_before

        seed_chatbot_knowledge(session)
        second_versions = {
            faq.id: faq.version for faq in session.exec(select(FAQ)).all()
        }
        assert second_versions == first_versions
        assert first_versions.keys() == faq_versions_before.keys()
        assert {
            ticket.id: (ticket.status, ticket.version, ticket.subject)
            for ticket in session.exec(select(Ticket)).all()
        } == tickets_before


def test_faq_seed_removes_bank_specific_content_and_unverified_sources(
    knowledge_seed_engine,
) -> None:
    from app.modules.conocimiento.models.category import TicketCategory

    with Session(knowledge_seed_engine) as session:
        seed_demo_data(session)
        category_ids_before = {
            category.id for category in session.exec(select(TicketCategory)).all()
        }
        faq_ids_before = {faq.id for faq in session.exec(select(FAQ)).all()}

        seed_chatbot_knowledge(session)

        categories = session.exec(select(TicketCategory)).all()
        active_faqs = session.exec(select(FAQ).where(FAQ.is_active.is_(True))).all()
        versions = session.exec(select(FAQVersion)).all()

    assert {category.id for category in categories} == category_ids_before
    assert {faq.id for faq in active_faqs} == faq_ids_before
    for faq in active_faqs:
        content = " ".join(
            str(getattr(faq, field) or "")
            for field in (
                "title",
                "question",
                "answer",
                "summary",
                "keywords",
                "tags",
                "synonyms",
                "normalized_content",
                "normalized_title",
                "normalized_question",
                "intent",
                "source_url",
            )
        )
        assert "gnb" not in content.casefold()
        if (
            faq.question
            == "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
        ):
            assert faq.source_url == SBS_CLAIMS_REGULATION_URL
            assert faq.source_verified_at is not None
        else:
            assert faq.source_url is None
            assert faq.source_verified_at is None
    assert all(
        (
            version.source_url == SBS_CLAIMS_REGULATION_URL
            and version.source_verified_at is not None
        )
        if version.question
        == "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
        else version.source_url is None and version.source_verified_at is None
        for version in versions
        if version.faq_id in faq_ids_before
    )


def test_seed_updates_matching_question_without_replacing_id_or_category(
    knowledge_seed_engine,
) -> None:
    with Session(knowledge_seed_engine) as session:
        seed_demo_data(session)
        faq = session.exec(
            select(FAQ).where(
                FAQ.question == "¿Qué requisitos necesito para solicitar una tarjeta?"
            )
        ).one()
        faq_id = faq.id
        category_id = faq.category_id
        faq.answer = "Respuesta anterior de ejemplo."
        session.add(faq)
        session.commit()

        seed_chatbot_knowledge(session)

        faq_after = session.get(FAQ, faq_id)
        faq_count = len(session.exec(select(FAQ)).all())

    assert faq_after is not None
    assert faq_after.id == faq_id
    assert faq_after.category_id == category_id
    assert faq_after.answer == FAQ_CARD_REQUIREMENTS_ANSWER
    assert faq_count == len(DEMO_FAQS)


def test_password_recovery_faq_does_not_claim_unverified_bank_specific_steps() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)
        faq = session.exec(
            select(FAQ).where(
                FAQ.question == "¿Qué hago si olvidé mi contraseña del portal?"
            )
        ).one()

        assert faq.answer == FAQ_PORTAL_PASSWORD_ANSWER
        assert faq.source_url is None
        assert faq.source_verified_at is None
