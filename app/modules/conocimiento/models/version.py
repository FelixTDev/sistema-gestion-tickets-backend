from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.modules.conocimiento.models.faq import FAQ
from app.modules.usuarios.models.user import utc_now


class FAQVersion(SQLModel, table=True):
    __tablename__ = "faq_versions"
    __table_args__ = (
        UniqueConstraint("faq_id", "version", name="uq_faq_versions_faq_version"),
        Index("ix_faq_versions_faq_version", "faq_id", "version"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    faq_id: str = Field(foreign_key="faqs.id", max_length=36)
    version: int = Field(sa_column=Column(Integer, nullable=False))
    category_id: str = Field(foreign_key="ticket_categories.id", max_length=36)
    title: str = Field(sa_column=Column(String(200), nullable=False))
    question: str = Field(sa_column=Column(Text, nullable=False))
    answer: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = Field(sa_column=Column(String(1000), nullable=False))
    keywords: str = Field(sa_column=Column(Text, nullable=False))
    tags: str = Field(sa_column=Column(Text, nullable=False))
    synonyms: str = Field(sa_column=Column(Text, nullable=False))
    normalized_content: str = Field(sa_column=Column(Text, nullable=False))
    intent: str | None = Field(default=None, max_length=100)
    status: FAQ.Status = Field(sa_column=Column(Enum(FAQ.Status), nullable=False))
    priority: int = Field(sa_column=Column(Integer, nullable=False))
    display_order: int = Field(sa_column=Column(Integer, nullable=False))
    is_active: bool = Field(nullable=False)
    published_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    unpublished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    changed_by: str = Field(foreign_key="users.id", max_length=36)
    action: str = Field(sa_column=Column(String(40), nullable=False))
    changed_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
