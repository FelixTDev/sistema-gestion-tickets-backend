from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Enum, Index, Integer, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"
    ESCALATED = "ESCALATED"
    CONVERTED_TO_TICKET = "CONVERTED_TO_TICKET"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_status_activity", "status", "last_activity_at"),
        Index("ix_conversations_user_activity", "user_id", "last_activity_at"),
        Index("ix_conversations_anonymous_activity", "anonymous_key", "started_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    user_id: str | None = Field(default=None, foreign_key="users.id", max_length=36)
    anonymous_key: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    status: ConversationStatus = Field(
        default=ConversationStatus.ACTIVE,
        sa_column=Column(Enum(ConversationStatus), nullable=False),
    )
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    ended_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    context_json: dict[str, object] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    detected_intent: str | None = Field(
        default=None, sa_column=Column(String(100), nullable=True)
    )
    last_faq_id: str | None = Field(default=None, foreign_key="faqs.id", max_length=36)
    category_id: str | None = Field(
        default=None, foreign_key="ticket_categories.id", max_length=36
    )
    pending_question: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    turn_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    last_confidence: float | None = Field(default=None)
    escalation_reason: str | None = Field(
        default=None, sa_column=Column(String(100), nullable=True)
    )
    escalated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    converted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_activity_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
