"""Add persistent in-app notifications.

Revision ID: 0005_notifications
Revises: 0004_ticket_list_indexes
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_notifications"
down_revision = "0004_ticket_list_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "TICKET_CREATED",
                "TICKET_STATUS_CHANGED",
                "TICKET_ASSIGNED",
                "TICKET_COMMENTED",
                "TICKET_REOPENED",
                "TICKET_CLOSED",
                "PASSWORD_CHANGED",
                "SECURITY_EVENT",
                "SLA_WARNING",
                "SLA_BREACHED",
                name="notificationtype",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("related_ticket_id", sa.String(length=36), nullable=True),
        sa.Column("related_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["related_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["related_conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
    )
    op.create_index(
        "ix_notifications_recipient_read_created",
        "notifications",
        ["recipient_user_id", "is_read", "created_at"],
    )
    op.create_index(
        "ix_notifications_recipient_user_id", "notifications", ["recipient_user_id"]
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_recipient_user_id", table_name="notifications")
    op.drop_index("ix_notifications_recipient_read_created", table_name="notifications")
    op.drop_table("notifications")
