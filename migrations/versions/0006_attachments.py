"""Add secure attachment metadata.

Revision ID: 0006_attachments
Revises: 0005_notifications
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_attachments"
down_revision = "0005_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("comment_id", sa.String(length=36), nullable=True),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("mime_type_declared", sa.String(length=120), nullable=False),
        sa.Column("mime_type_detected", sa.String(length=120), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "DELETED", name="attachmentstatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["comment_id"], ["ticket_comments.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("storage_key", name="uq_attachments_storage_key"),
    )
    op.create_index(
        "ix_attachments_ticket_created", "attachments", ["ticket_id", "created_at"]
    )
    op.create_index("ix_attachments_comment_id", "attachments", ["comment_id"])
    op.create_index(
        "ix_attachments_uploaded_by_user_id", "attachments", ["uploaded_by_user_id"]
    )
    op.create_index("ix_attachments_sha256", "attachments", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_attachments_sha256", table_name="attachments")
    op.drop_index("ix_attachments_uploaded_by_user_id", table_name="attachments")
    op.drop_index("ix_attachments_comment_id", table_name="attachments")
    op.drop_index("ix_attachments_ticket_created", table_name="attachments")
    op.drop_table("attachments")
