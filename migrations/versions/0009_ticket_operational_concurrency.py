"""Add ticket update timestamps and optimistic concurrency version."""

import sqlalchemy as sa
from alembic import op

revision = "0009_ticket_ops"
down_revision = "0008_user_profile_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    column_names = {column["name"] for column in inspector.get_columns("tickets")}
    if "updated_at" not in column_names:
        op.add_column(
            "tickets",
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
    if "version" not in column_names:
        op.add_column(
            "tickets",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )
    index_names = {index["name"] for index in inspector.get_indexes("tickets")}
    if "ix_tickets_updated_id" not in index_names:
        op.create_index("ix_tickets_updated_id", "tickets", ["updated_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_updated_id", table_name="tickets")
    op.drop_column("tickets", "version")
    op.drop_column("tickets", "updated_at")
