from collections.abc import Generator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db import models  # noqa: F401
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.version import FAQVersion
from app.modules.tickets.models.ticket import Ticket
from app.seed.demo_data import DEMO_FAQS, seed_chatbot_knowledge, seed_demo_data


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


def test_safe_knowledge_seed_is_source_traced_and_does_not_rewrite_tickets(
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
        assert all(
            faq.source_url is not None
            and faq.source_url.startswith("https://www.bancognb.com.pe/")
            and faq.source_verified_at is not None
            for faq in published
            if faq.question in {item[1] for item in DEMO_FAQS}
        )
        assert all(
            snapshot.source_url is not None and snapshot.source_verified_at is not None
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
