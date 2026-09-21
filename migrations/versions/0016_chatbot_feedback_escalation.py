"""Store optional chatbot escalation feedback."""

import sqlalchemy as sa
from alembic import op

revision = "0016_chatbot_feedback_escalation"
down_revision = "0015_chatbot_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faq_feedback",
        sa.Column("escalation_accepted", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("faq_feedback", "escalation_accepted")
