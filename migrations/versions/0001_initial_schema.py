"""Create initial MVP schema.

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def uid():
    return sa.String(length=36)


def upgrade() -> None:
    op.create_table("roles", sa.Column("id", uid(), primary_key=True), sa.Column("name", sa.String(30), nullable=False, unique=True), sa.Column("description", sa.Text(), nullable=False))
    op.create_table("ticket_categories", sa.Column("id", uid(), primary_key=True), sa.Column("name", sa.String(80), nullable=False, unique=True), sa.Column("description", sa.Text(), nullable=False), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("users", sa.Column("id", uid(), primary_key=True), sa.Column("role_id", uid(), sa.ForeignKey("roles.id"), nullable=False), sa.Column("full_name", sa.String(150), nullable=False), sa.Column("email", sa.String(255), nullable=False, unique=True), sa.Column("password_hash", sa.Text(), nullable=False), sa.Column("phone", sa.String(30)), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("conversations", sa.Column("id", uid(), primary_key=True), sa.Column("user_id", uid(), sa.ForeignKey("users.id")), sa.Column("status", sa.Enum("ACTIVE", "CLOSED", name="conversationstatus"), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ended_at", sa.DateTime(timezone=True)))
    op.create_table("faqs", sa.Column("id", uid(), primary_key=True), sa.Column("category_id", uid(), sa.ForeignKey("ticket_categories.id"), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("answer", sa.Text(), nullable=False), sa.Column("keywords", sa.Text(), nullable=False, server_default=""), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_by", uid(), sa.ForeignKey("users.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("chat_messages", sa.Column("id", uid(), primary_key=True), sa.Column("conversation_id", uid(), sa.ForeignKey("conversations.id"), nullable=False), sa.Column("sender_type", sa.Enum("USER", "BOT", "SYSTEM", name="sendertype"), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("intent", sa.String(100)), sa.Column("confidence", sa.Numeric(5, 4)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("tickets", sa.Column("id", uid(), primary_key=True), sa.Column("tracking_code", sa.String(30), nullable=False, unique=True), sa.Column("client_id", uid(), sa.ForeignKey("users.id"), nullable=False), sa.Column("conversation_id", uid(), sa.ForeignKey("conversations.id"), unique=True), sa.Column("category_id", uid(), sa.ForeignKey("ticket_categories.id"), nullable=False), sa.Column("subject", sa.String(200), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("priority", sa.Enum("BAJA", "MEDIA", "ALTA", "URGENTE", name="ticketpriority"), nullable=False), sa.Column("status", sa.Enum("NUEVO", "ASIGNADO", "EN_PROCESO", "PENDIENTE_CLIENTE", "RESUELTO", "CERRADO", "CANCELADO", name="ticketstatus"), nullable=False), sa.Column("source", sa.Enum("CHATBOT", "MANUAL", name="ticketsource"), nullable=False), sa.Column("assigned_advisor_id", uid(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("assigned_at", sa.DateTime(timezone=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)), sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.create_table("ticket_comments", sa.Column("id", uid(), primary_key=True), sa.Column("ticket_id", uid(), sa.ForeignKey("tickets.id"), nullable=False), sa.Column("author_id", uid(), sa.ForeignKey("users.id"), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ticket_assignments", sa.Column("id", uid(), primary_key=True), sa.Column("ticket_id", uid(), sa.ForeignKey("tickets.id"), nullable=False), sa.Column("advisor_id", uid(), sa.ForeignKey("users.id"), nullable=False), sa.Column("assigned_by", uid(), sa.ForeignKey("users.id"), nullable=False), sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False), sa.Column("unassigned_at", sa.DateTime(timezone=True)))
    op.create_table("ticket_history", sa.Column("id", uid(), primary_key=True), sa.Column("ticket_id", uid(), sa.ForeignKey("tickets.id"), nullable=False), sa.Column("actor_id", uid(), sa.ForeignKey("users.id")), sa.Column("action", sa.String(50), nullable=False), sa.Column("old_value", sa.Text()), sa.Column("new_value", sa.Text()), sa.Column("description", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    for table in ("ticket_history", "ticket_assignments", "ticket_comments", "tickets", "chat_messages", "faqs", "conversations", "users", "ticket_categories", "roles"):
        op.drop_table(table)
