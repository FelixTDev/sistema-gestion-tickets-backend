"""Add timestamps to knowledge categories.

Revision ID: 0002_category_timestamps
Revises: 0001_initial_schema
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_category_timestamps"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ticket_categories",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "ticket_categories",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.alter_column("ticket_categories", "created_at", server_default=None)
    op.alter_column("ticket_categories", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("ticket_categories", "updated_at")
    op.drop_column("ticket_categories", "created_at")
