"""Add authentication recovery, verification, rate limiting and sessions.

Revision ID: 0003_auth_security
Revises: 0002_category_timestamps
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth_security"
down_revision = "0002_category_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("sessions_invalidated_at", sa.DateTime(timezone=True)),
    )
    op.alter_column("users", "email_verified", server_default=None)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum("PASSWORD_RESET", "EMAIL_VERIFICATION", name="authtokenpurpose"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])

    op.create_table(
        "auth_rate_limits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("action", "key_hash", name="uq_auth_rate_limit_key"),
    )
    op.create_index("ix_auth_rate_limits_key_hash", "auth_rate_limits", ["key_hash"])


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limits_key_hash", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")
    op.drop_index("ix_auth_tokens_token_hash", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_column("users", "sessions_invalidated_at")
    op.drop_column("users", "email_verified")
