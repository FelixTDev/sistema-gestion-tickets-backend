"""Add deterministic chatbot context, states and feedback linkage."""

import sqlalchemy as sa
from alembic import op

revision = "0015_chatbot_context"
down_revision = "0014_global_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("context_json", sa.JSON(), nullable=True))
    connection = op.get_bind()
    if connection.dialect.name == "mysql":
        op.execute(
            "UPDATE conversations SET context_json = JSON_OBJECT() "
            "WHERE context_json IS NULL"
        )
    else:
        op.execute(
            "UPDATE conversations SET context_json = '{}' WHERE context_json IS NULL"
        )
    op.alter_column(
        "conversations",
        "context_json",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.add_column(
        "conversations", sa.Column("anonymous_key", sa.String(64), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("detected_intent", sa.String(100), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("last_faq_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("category_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("pending_question", sa.Text(), nullable=True)
    )
    op.add_column(
        "conversations",
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversations", sa.Column("last_confidence", sa.Float(), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("escalation_reason", sa.String(100), nullable=True)
    )
    op.add_column(
        "conversations", sa.Column("escalated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "conversations", sa.Column("converted_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "conversations",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_foreign_key(
        "fk_conversations_last_faq_id",
        "conversations",
        "faqs",
        ["last_faq_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_conversations_category_id",
        "conversations",
        "ticket_categories",
        ["category_id"],
        ["id"],
    )
    op.create_index(
        "ix_conversations_status_activity",
        "conversations",
        ["status", "last_activity_at"],
    )
    op.create_index(
        "ix_conversations_user_activity",
        "conversations",
        ["user_id", "last_activity_at"],
    )
    op.create_index(
        "ix_conversations_anonymous_activity",
        "conversations",
        ["anonymous_key", "started_at"],
    )

    op.add_column(
        "chat_messages",
        sa.Column("faq_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_messages_faq_id", "chat_messages", "faqs", ["faq_id"], ["id"]
    )
    op.create_index(
        "ix_chat_messages_conversation_created",
        "chat_messages",
        ["conversation_id", "created_at"],
    )

    if connection.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE conversations MODIFY status ENUM("
            "'ACTIVE','WAITING_CLARIFICATION','ESCALATED','CONVERTED_TO_TICKET',"
            "'CLOSED','EXPIRED') NOT NULL"
        )
        op.execute(
            "ALTER TABLE notifications MODIFY type ENUM("
            "'TICKET_CREATED','TICKET_STATUS_CHANGED','TICKET_ASSIGNED',"
            "'TICKET_COMMENTED','TICKET_REOPENED','TICKET_CLOSED','PASSWORD_CHANGED',"
            "'SECURITY_EVENT','SLA_WARNING','SLA_BREACHED','CHAT_ESCALATED') NOT NULL"
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE notifications MODIFY type ENUM("
            "'TICKET_CREATED','TICKET_STATUS_CHANGED','TICKET_ASSIGNED',"
            "'TICKET_COMMENTED','TICKET_REOPENED','TICKET_CLOSED','PASSWORD_CHANGED',"
            "'SECURITY_EVENT','SLA_WARNING','SLA_BREACHED') NOT NULL"
        )
        op.execute(
            "ALTER TABLE conversations MODIFY status ENUM('ACTIVE','CLOSED') NOT NULL"
        )
    op.drop_index("ix_chat_messages_conversation_created", table_name="chat_messages")
    op.drop_constraint("fk_chat_messages_faq_id", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "faq_id")
    op.drop_index("ix_conversations_user_activity", table_name="conversations")
    op.drop_index("ix_conversations_status_activity", table_name="conversations")
    op.drop_index("ix_conversations_anonymous_activity", table_name="conversations")
    op.drop_constraint(
        "fk_conversations_category_id", "conversations", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_conversations_last_faq_id", "conversations", type_="foreignkey"
    )
    for column in (
        "last_activity_at",
        "converted_at",
        "escalated_at",
        "escalation_reason",
        "last_confidence",
        "turn_count",
        "pending_question",
        "category_id",
        "last_faq_id",
        "detected_intent",
        "context_json",
        "anonymous_key",
    ):
        op.drop_column("conversations", column)
