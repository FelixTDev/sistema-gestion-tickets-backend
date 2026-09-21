"""Add append-only global audit logs."""

import sqlalchemy as sa
from alembic import op

revision = "0014_global_audit"
down_revision = "0013_report_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("actor_role", sa.String(length=30), nullable=True),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=True),
        sa.Column("target_user_id", sa.String(length=36), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])
    op.create_index(
        "ix_audit_logs_actor_occurred", "audit_logs", ["actor_user_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_logs_resource_occurred",
        "audit_logs",
        ["resource_type", "resource_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_logs_event_occurred", "audit_logs", ["event_type", "occurred_at"]
    )
    op.create_index(
        "ix_audit_logs_action_occurred", "audit_logs", ["action", "occurred_at"]
    )
    op.create_index(
        "ix_audit_logs_target_occurred", "audit_logs", ["target_user_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_logs_success_occurred", "audit_logs", ["success", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_success_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_target_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_occurred", table_name="audit_logs")
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_table("audit_logs")
