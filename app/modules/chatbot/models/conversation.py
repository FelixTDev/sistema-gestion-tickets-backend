from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    user_id: str | None = Field(default=None, foreign_key="users.id", max_length=36)
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
