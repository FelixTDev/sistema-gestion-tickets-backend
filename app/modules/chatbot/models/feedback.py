from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class ChatbotFeedback(SQLModel, table=True):
    __tablename__ = "chatbot_feedback"
    __table_args__ = (
        Index("ix_chatbot_feedback_created_at", "created_at"),
        Index("ix_chatbot_feedback_helpful", "is_helpful"),
        Index("ix_chatbot_feedback_faq_created", "faq_id", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    conversation_id: str = Field(foreign_key="conversations.id", max_length=36)
    message_id: str = Field(
        sa_column=Column(
            String(36), ForeignKey("chat_messages.id"), nullable=False, unique=True
        )
    )
    faq_id: str | None = Field(default=None, foreign_key="faqs.id", max_length=36)
    faq_feedback_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(36), ForeignKey("faq_feedback.id"), nullable=True, unique=True
        ),
    )
    user_id: str | None = Field(default=None, foreign_key="users.id", max_length=36)
    is_helpful: bool = Field(sa_column=Column(Boolean, nullable=False))
    escalation_accepted: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
