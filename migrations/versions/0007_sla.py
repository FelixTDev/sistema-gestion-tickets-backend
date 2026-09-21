"""Add configurable ticket SLA policies and tracking."""

import sqlalchemy as sa
from alembic import op

revision = "0007_sla"
down_revision = "0006_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sla_policies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_key", sa.String(length=220), nullable=False),
        sa.Column(
            "priority",
            sa.Enum("BAJA", "MEDIA", "ALTA", "URGENTE", name="ticketpriority"),
            nullable=True,
        ),
        sa.Column("category_id", sa.String(length=36), nullable=True),
        sa.Column(
            "source",
            sa.Enum("CHATBOT", "MANUAL", name="ticketsource"),
            nullable=True,
        ),
        sa.Column("first_response_seconds", sa.Integer(), nullable=False),
        sa.Column("resolution_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_seconds", sa.Integer(), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("calendar_name", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["ticket_categories.id"]),
        sa.UniqueConstraint("policy_key", name="uq_sla_policies_policy_key"),
    )
    op.create_index(
        "ix_sla_policies_scope",
        "sla_policies",
        ["is_active", "priority", "category_id", "source"],
    )

    op.create_table(
        "ticket_slas",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_responded_at", sa.DateTime(timezone=True)),
        sa.Column("first_response_within_sla", sa.Boolean()),
        sa.Column("first_response_warning_sent_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_warning_sent_at", sa.DateTime(timezone=True)),
        sa.Column("first_response_breached_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("total_paused_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "PAUSED",
                "COMPLETED",
                "BREACHED",
                "CANCELLED",
                name="slastatus",
            ),
            nullable=False,
        ),
        sa.Column("warning_sent_at", sa.DateTime(timezone=True)),
        sa.Column("warning_stage", sa.String(length=30)),
        sa.Column("breached_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_breached_at", sa.DateTime(timezone=True)),
        sa.Column("completed_within_sla", sa.Boolean()),
        sa.Column("reopen_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["sla_policies.id"]),
        sa.UniqueConstraint("ticket_id", name="uq_ticket_slas_ticket_id"),
    )
    op.create_index(
        "ix_ticket_slas_status_resolution_due",
        "ticket_slas",
        ["status", "resolution_due_at"],
    )
    op.create_index(
        "ix_ticket_slas_status_first_response_due",
        "ticket_slas",
        ["status", "first_response_due_at"],
    )
    op.create_index("ix_ticket_slas_ticket_id", "ticket_slas", ["ticket_id"])

    op.create_table(
        "sla_policy_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("old_values", sa.JSON(), nullable=False),
        sa.Column("new_values", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["sla_policies.id"]),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.create_index(
        "ix_sla_policy_history_policy_created",
        "sla_policy_history",
        ["policy_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sla_policy_history_policy_created", table_name="sla_policy_history"
    )
    op.drop_table("sla_policy_history")
    op.drop_index("ix_ticket_slas_ticket_id", table_name="ticket_slas")
    op.drop_index("ix_ticket_slas_status_first_response_due", table_name="ticket_slas")
    op.drop_index("ix_ticket_slas_status_resolution_due", table_name="ticket_slas")
    op.drop_table("ticket_slas")
    op.drop_index("ix_sla_policies_scope", table_name="sla_policies")
    op.drop_table("sla_policies")
