from sqlmodel import SQLModel


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
