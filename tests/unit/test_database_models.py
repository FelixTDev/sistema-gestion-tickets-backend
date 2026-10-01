from sqlmodel import SQLModel

from app.modules.tickets.models.ticket import Ticket


def test_all_mvp_tables_are_registered():
    import app.db.models  # noqa: F401

    expected = {
        "roles",
        "users",
        "ticket_categories",
        "conversations",
        "chat_messages",
        "faqs",
        "tickets",
        "ticket_comments",
        "ticket_assignments",
        "ticket_history",
    }

    assert expected.issubset(SQLModel.metadata.tables)


def test_ticket_model_declares_assignment_state_constraint():
    constraints = {
        constraint.name: constraint for constraint in Ticket.__table__.constraints
    }

    constraint = constraints["ck_tickets_assignment_state"]
    sql = str(constraint.sqltext)

    assert "NUEVO" in sql
    assert "assigned_advisor_id IS NULL" in sql
    assert "ASIGNADO" in sql
    assert "EN_PROCESO" in sql
    assert "PENDIENTE_CLIENTE" in sql
    assert "RESUELTO" in sql
    assert "assigned_advisor_id IS NOT NULL" in sql
