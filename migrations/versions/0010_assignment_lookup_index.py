"""Add an index for active ticket assignment lookups."""

import sqlalchemy as sa
from alembic import op

revision = "0010_assignment_idx"
down_revision = "0009_ticket_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    index_names = {
        index["name"] for index in inspector.get_indexes("ticket_assignments")
    }
    if "ix_ticket_assignments_ticket_active" not in index_names:
        op.create_index(
            "ix_ticket_assignments_ticket_active",
            "ticket_assignments",
            ["ticket_id", "unassigned_at", "assigned_at"],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_ticket_assignments_ticket_active", table_name="ticket_assignments"
    )
