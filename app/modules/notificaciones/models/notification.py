from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class NotificationType(StrEnum):
    TICKET_CREATED = "ticket_created"
    TICKET_STATUS_CHANGED = "ticket_status_changed"
    TICKET_ASSIGNED = "ticket_assigned"
    TICKET_COMMENTED = "ticket_commented"
    TICKET_REOPENED = "ticket_reopened"
    TICKET_CLOSED = "ticket_closed"
    PASSWORD_CHANGED = "password_changed"
    SECURITY_EVENT = "security_event"
    SLA_WARNING = "sla_warning"
    SLA_BREACHED = "sla_breached"
    CHAT_ESCALATED = "chat_escalated"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_recipient_read_created",
            "recipient_user_id",
            "is_read",
            "created_at",
        ),
        Index("ix_notifications_recipient_user_id", "recipient_user_id"),
        Index("ix_notifications_created_at", "created_at"),
        UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    recipient_user_id: str = Field(foreign_key="users.id", max_length=36)
    notification_type: NotificationType = Field(
        sa_column=Column("type", Enum(NotificationType), nullable=False)
    )
    title: str = Field(sa_column=Column(String(160), nullable=False))
    message: str = Field(sa_column=Column(Text, nullable=False))
    related_ticket_id: str | None = Field(
        default=None, foreign_key="tickets.id", max_length=36
    )
    related_conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", max_length=36
    )
    is_read: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    read_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    metadata_json: dict[str, object] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    dedupe_key: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )
