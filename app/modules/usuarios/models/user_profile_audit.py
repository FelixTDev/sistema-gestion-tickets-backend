from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class UserProfileAudit(SQLModel, table=True):
    __tablename__ = "user_profile_audits"
    __table_args__ = (
        Index(
            "ix_user_profile_audits_affected_created", "affected_user_id", "created_at"
        ),
        Index("ix_user_profile_audits_actor_created", "actor_id", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    actor_id: str = Field(foreign_key="users.id", max_length=36)
    affected_user_id: str = Field(foreign_key="users.id", max_length=36)
    field_name: str = Field(sa_column=Column(String(64), nullable=False))
    old_value_redacted: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    new_value_redacted: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
