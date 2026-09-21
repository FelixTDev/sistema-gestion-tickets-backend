from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class AuthTokenPurpose(StrEnum):
    PASSWORD_RESET = "PASSWORD_RESET"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"


class AuthToken(SQLModel, table=True):
    __tablename__ = "auth_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),)

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    user_id: str = Field(foreign_key="users.id", max_length=36, index=True)
    purpose: AuthTokenPurpose = Field(
        sa_column=Column(Enum(AuthTokenPurpose), nullable=False)
    )
    token_hash: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    used_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
