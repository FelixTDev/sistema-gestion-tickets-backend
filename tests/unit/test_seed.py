import re

from sqlmodel import Session, create_engine, select


def test_demo_seed_is_idempotent():
    from app.db.models import Role, User
    from app.seed.demo_data import seed_demo_data

    engine = create_engine("sqlite://")
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session)
        seed_demo_data(session)
        users = session.exec(select(User)).all()
        roles = session.exec(select(Role)).all()

    assert len(users) == 3
    assert {user.email for user in users} == {
        "cliente@demo.com",
        "asesor@demo.com",
        "supervisor@demo.com",
    }
    assert len(roles) == 3


def test_chatbot_demo_seed_is_sqlite_only_and_isolates_operational_tickets():
    from sqlmodel import SQLModel

    from app.db.models import Role, Ticket, User
    from app.modules.tickets.models.ticket import TicketStatus
    from app.seed.chatbot_demo_data import seed_chatbot_demo_data

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_chatbot_demo_data(session)
        seed_chatbot_demo_data(session)
        users = {user.email: user for user in session.exec(select(User)).all()}
        roles = {role.name for role in session.exec(select(Role)).all()}
        tickets = session.exec(select(Ticket)).all()

    assert roles == {"CLIENTE", "ASESOR", "SUPERVISOR"}
    assert {"cliente@demo.com", "cliente2@demo.com"}.issubset(users)
    assert {"asesor@demo.com", "supervisor@demo.com"}.issubset(users)
    assert len(tickets) == 6
    assert {ticket.tracking_code for ticket in tickets} == {
        f"TCK-LOCAL-{number:03d}" for number in range(1, 7)
    }
    assert TicketStatus.PENDIENTE_CLIENTE in {ticket.status for ticket in tickets}
    assert TicketStatus.RESUELTO in {ticket.status for ticket in tickets}
    assert all(
        ticket.client_id == users["cliente@demo.com"].id
        for ticket in tickets
        if ticket.tracking_code != "TCK-LOCAL-006"
    )
    assert (
        next(
            ticket for ticket in tickets if ticket.tracking_code == "TCK-LOCAL-006"
        ).client_id
        == users["cliente2@demo.com"].id
    )


def test_demo_seed_populates_operational_data_without_forbidden_text():
    from sqlmodel import SQLModel

    from app.db.models import (
        FAQ,
        ChatMessage,
        Conversation,
        Role,
        Ticket,
        TicketAssignment,
        TicketCategory,
        TicketComment,
        TicketHistory,
        User,
    )
    from app.modules.conocimiento.services.faq_content_policy import (
        APPROVED_SOURCELESS_ANSWERS,
        FAQ_CLAIM_VS_REQUEST_ANSWER,
    )
    from app.modules.tickets.models.ticket import (
        TicketPriority,
        TicketSource,
        TicketStatus,
    )
    from app.seed.demo_data import DEMO_FAQS, seed_demo_data

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session)
        seed_demo_data(session)
        counts = {
            "roles": len(session.exec(select(Role)).all()),
            "users": len(session.exec(select(User)).all()),
            "categories": len(session.exec(select(TicketCategory)).all()),
            "faqs": len(session.exec(select(FAQ)).all()),
            "tickets": len(session.exec(select(Ticket)).all()),
            "comments": len(session.exec(select(TicketComment)).all()),
            "history": len(session.exec(select(TicketHistory)).all()),
            "assignments": len(session.exec(select(TicketAssignment)).all()),
            "conversations": len(session.exec(select(Conversation)).all()),
            "messages": len(session.exec(select(ChatMessage)).all()),
        }
        tickets = session.exec(select(Ticket)).all()
        faqs = session.exec(select(FAQ)).all()
        visible_values = []
        for model in (
            TicketCategory,
            Ticket,
            TicketComment,
            TicketHistory,
            ChatMessage,
        ):
            for row in session.exec(select(model)).all():
                visible_values.extend(
                    str(getattr(row, field))
                    for field in (
                        "name",
                        "description",
                        "question",
                        "answer",
                        "keywords",
                        "subject",
                        "content",
                    )
                    if getattr(row, field, None) is not None
                )

    assert counts == {
        "roles": 3,
        "users": 3,
        "categories": 7,
        "faqs": len(DEMO_FAQS),
        "tickets": 7,
        "comments": 8,
        "history": 35,
        "assignments": 5,
        "conversations": 2,
        "messages": 4,
    }
    assert {ticket.status for ticket in tickets} == set(TicketStatus)
    assert {ticket.priority for ticket in tickets} == set(TicketPriority)
    assert {ticket.source for ticket in tickets} == {
        TicketSource.MANUAL,
        TicketSource.CHATBOT,
    }
    assert {faq.question for faq in faqs if faq.is_active} == {
        entry[1] for entry in DEMO_FAQS
    }
    assert "¿Cómo me afilio a Banca Móvil?" not in {
        faq.question for faq in faqs if faq.is_active
    }
    assert all(
        faq.answer in APPROVED_SOURCELESS_ANSWERS for faq in faqs if faq.is_active
    )
    claim_faq = next(
        faq
        for faq in faqs
        if faq.question == "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
    )
    assert claim_faq.answer == FAQ_CLAIM_VS_REQUEST_ANSWER
    assert claim_faq.source_url is not None
    assert claim_faq.source_verified_at is not None
    assert all(
        not any(
            term in value.casefold()
            for term in ("prototipo", "fictici", "simulad", "académic")
        )
        and re.search(r"\bdemo\b", value.casefold()) is None
        for value in visible_values
    )
