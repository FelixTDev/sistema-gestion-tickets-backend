"""Add user profile preferences and redacted profile audit records."""

import sqlalchemy as sa
from alembic import op

revision = "0008_user_profile_preferences"
down_revision = "0007_sla"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "assignment_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "status_change_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "comment_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "sla_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "preferred_language",
            sa.String(length=10),
            nullable=False,
            server_default="es",
        ),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_table(
        "user_profile_audits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("affected_user_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("old_value_redacted", sa.Text(), nullable=True),
        sa.Column("new_value_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["affected_user_id"], ["users.id"]),
    )
    op.create_index(
        "ix_user_profile_audits_affected_created",
        "user_profile_audits",
        ["affected_user_id", "created_at"],
    )
    op.create_index(
        "ix_user_profile_audits_actor_created",
        "user_profile_audits",
        ["actor_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_profile_audits_actor_created", table_name="user_profile_audits"
    )
    op.drop_index(
        "ix_user_profile_audits_affected_created", table_name="user_profile_audits"
    )
    op.drop_table("user_profile_audits")
    op.drop_table("user_preferences")
