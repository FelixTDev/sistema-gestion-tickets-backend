from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum, Index, Integer, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class FAQ(SQLModel, table=True):
    __tablename__ = "faqs"
    __table_args__ = (
        Index(
            "ix_faqs_editorial_scope",
            "status",
            "is_active",
            "category_id",
            "published_at",
        ),
        Index("ix_faqs_updated_at", "updated_at"),
    )

    class Status(StrEnum):
        DRAFT = "DRAFT"
        REVIEW = "REVIEW"
        PUBLISHED = "PUBLISHED"
        ARCHIVED = "ARCHIVED"

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    category_id: str = Field(foreign_key="ticket_categories.id", max_length=36)
    title: str = Field(
        default="", sa_column=Column(String(200), nullable=False, server_default="")
    )
    question: str = Field(sa_column=Column(Text, nullable=False))
    answer: str = Field(sa_column=Column(Text, nullable=False))
    summary: str = Field(
        default="", sa_column=Column(String(1000), nullable=False, server_default="")
    )
    keywords: str = Field(sa_column=Column(Text, nullable=False, default=""))
    tags: str = Field(
        default="[]", sa_column=Column(Text, nullable=False, server_default="[]")
    )
    synonyms: str = Field(
        default="[]", sa_column=Column(Text, nullable=False, server_default="[]")
    )
    normalized_content: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    normalized_title: str = Field(
        default="", sa_column=Column(String(200), nullable=False, server_default="")
    )
    normalized_question: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    intent: str | None = Field(default=None, max_length=100)
    status: Status = Field(
        default=Status.PUBLISHED,
        sa_column=Column(Enum(Status), nullable=False, server_default="PUBLISHED"),
    )
    priority: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    display_order: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    version: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default="1")
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    created_by: str = Field(foreign_key="users.id", max_length=36)
    updated_by: str | None = Field(default=None, foreign_key="users.id", max_length=36)
    published_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    unpublished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
