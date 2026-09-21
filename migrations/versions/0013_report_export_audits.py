"""Add auditable report export attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0013_report_exports"
down_revision = "0012_knowledge_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_export_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("report_name", sa.String(length=64), nullable=False),
        sa.Column("export_format", sa.String(length=10), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_export_audits_actor_created",
        "report_export_audits",
        ["actor_id", "created_at"],
    )
    op.create_index(
        "ix_report_export_audits_report_created",
        "report_export_audits",
        ["report_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_report_export_audits_report_created", table_name="report_export_audits"
    )
    op.drop_index(
        "ix_report_export_audits_actor_created", table_name="report_export_audits"
    )
    op.drop_table("report_export_audits")
