from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    Index,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class AttachmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class Attachment(SQLModel, table=True):
    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_ticket_created", "ticket_id", "created_at"),
        Index("ix_attachments_comment_id", "comment_id"),
        Index("ix_attachments_uploaded_by_user_id", "uploaded_by_user_id"),
        Index("ix_attachments_sha256", "sha256"),
        UniqueConstraint("storage_key", name="uq_attachments_storage_key"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    ticket_id: str = Field(foreign_key="tickets.id", max_length=36)
    comment_id: str | None = Field(
        default=None, foreign_key="ticket_comments.id", max_length=36
    )
    uploaded_by_user_id: str = Field(foreign_key="users.id", max_length=36)
    original_filename: str = Field(sa_column=Column(String(255), nullable=False))
    storage_key: str = Field(sa_column=Column(String(255), nullable=False))
    mime_type_declared: str = Field(sa_column=Column(String(120), nullable=False))
    mime_type_detected: str = Field(sa_column=Column(String(120), nullable=False))
    file_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(sa_column=Column(String(64), nullable=False))
    status: AttachmentStatus = Field(
        default=AttachmentStatus.ACTIVE,
        sa_column=Column(Enum(AttachmentStatus), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
