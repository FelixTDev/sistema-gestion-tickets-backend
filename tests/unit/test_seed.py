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
    from app.modules.tickets.models.ticket import (
        TicketPriority,
        TicketSource,
        TicketStatus,
    )
    from app.seed.demo_data import seed_demo_data

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
        visible_values = []
        for model in (
            TicketCategory,
            FAQ,
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
        "faqs": 8,
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
    assert all(
        not any(
            term in value.casefold()
            for term in ("prototipo", "fictici", "simulad", "académic", "demo")
        )
        for value in visible_values
    )
