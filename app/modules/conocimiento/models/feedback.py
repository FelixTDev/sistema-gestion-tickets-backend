from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class FAQFeedback(SQLModel, table=True):
    __tablename__ = "faq_feedback"
    __table_args__ = (
        Index("ix_faq_feedback_faq_created", "faq_id", "created_at"),
        Index("ix_faq_feedback_faq_helpful", "faq_id", "is_helpful"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    faq_id: str = Field(foreign_key="faqs.id", max_length=36)
    user_id: str | None = Field(default=None, foreign_key="users.id", max_length=36)
    is_helpful: bool = Field(sa_column=Column(Boolean, nullable=False))
    escalation_accepted: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    comment: str | None = Field(default=None, sa_column=Column(String(500)))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
