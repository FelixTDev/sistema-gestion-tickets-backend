"""Add indexes for paginated ticket lists.

Revision ID: 0004_ticket_list_indexes
Revises: 0003_auth_security
"""

from alembic import op

revision = "0004_ticket_list_indexes"
down_revision = "0003_auth_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_tickets_client_created_id",
        "tickets",
        ["client_id", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_assigned_advisor_created_id",
        "tickets",
        ["assigned_advisor_id", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_status_created_id",
        "tickets",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_category_created_id",
        "tickets",
        ["category_id", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_priority_created_id",
        "tickets",
        ["priority", "created_at", "id"],
    )
    op.create_index(
        "ix_tickets_source_created_id",
        "tickets",
        ["source", "created_at", "id"],
    )
    op.create_index("ix_tickets_created_id", "tickets", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_created_id", table_name="tickets")
    op.drop_index("ix_tickets_source_created_id", table_name="tickets")
    op.drop_index("ix_tickets_priority_created_id", table_name="tickets")
    op.drop_index("ix_tickets_category_created_id", table_name="tickets")
    op.drop_index("ix_tickets_status_created_id", table_name="tickets")
    op.drop_index("ix_tickets_assigned_advisor_created_id", table_name="tickets")
    op.drop_index("ix_tickets_client_created_id", table_name="tickets")
