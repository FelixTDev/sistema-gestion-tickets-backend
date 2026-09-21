from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Enum, Index, Numeric, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class SenderType(StrEnum):
    USER = "USER"
    BOT = "BOT"
    SYSTEM = "SYSTEM"


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=36)
    sender_type: SenderType = Field(sa_column=Column(Enum(SenderType), nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    intent: str | None = Field(default=None, max_length=100)
    confidence: float | None = Field(
        default=None, sa_column=Column(Numeric(5, 4), nullable=True)
    )
    faq_id: str | None = Field(default=None, foreign_key="faqs.id", max_length=36)
    response_source: str | None = Field(
        default=None, sa_column=Column(String(30), nullable=True)
    )
    ai_trace_json: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
