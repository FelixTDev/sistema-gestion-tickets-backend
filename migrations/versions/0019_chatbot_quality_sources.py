"""Persist chatbot feedback and official FAQ source provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0019_chatbot_quality_sources"
down_revision = "0018_ticket_assignment_invariant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faqs", sa.Column("source_url", sa.String(length=2048), nullable=True)
    )
    op.add_column(
        "faqs",
        sa.Column("source_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "faq_versions", sa.Column("source_url", sa.String(length=2048), nullable=True)
    )
    op.add_column(
        "faq_versions",
        sa.Column("source_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "chatbot_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("faq_id", sa.String(length=36), nullable=True),
        sa.Column("faq_feedback_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("is_helpful", sa.Boolean(), nullable=False),
        sa.Column("escalation_accepted", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["faq_feedback_id"], ["faq_feedback.id"]),
        sa.ForeignKeyConstraint(["faq_id"], ["faqs.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("faq_feedback_id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        "ix_chatbot_feedback_created_at", "chatbot_feedback", ["created_at"]
    )
    op.create_index("ix_chatbot_feedback_helpful", "chatbot_feedback", ["is_helpful"])
    op.create_index(
        "ix_chatbot_feedback_faq_created",
        "chatbot_feedback",
        ["faq_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chatbot_feedback_faq_created", table_name="chatbot_feedback")
    op.drop_index("ix_chatbot_feedback_helpful", table_name="chatbot_feedback")
    op.drop_index("ix_chatbot_feedback_created_at", table_name="chatbot_feedback")
    op.drop_table("chatbot_feedback")
    op.drop_column("faq_versions", "source_verified_at")
    op.drop_column("faq_versions", "source_url")
    op.drop_column("faqs", "source_verified_at")
    op.drop_column("faqs", "source_url")
