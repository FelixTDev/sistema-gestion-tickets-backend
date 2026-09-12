from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, Numeric, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class SenderType(StrEnum):
    USER = "USER"
    BOT = "BOT"
    SYSTEM = "SYSTEM"


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

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
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
