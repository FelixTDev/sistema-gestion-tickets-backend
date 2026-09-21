"""Persist safe chatbot response mode and AI trace metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0017_chatbot_ai_trace"
down_revision = "0016_chatbot_feedback_escalation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages", sa.Column("response_source", sa.String(30), nullable=True)
    )
    op.add_column("chat_messages", sa.Column("ai_trace_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "ai_trace_json")
    op.drop_column("chat_messages", "response_source")
