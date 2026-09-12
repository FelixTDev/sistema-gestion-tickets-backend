from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    role_id: str = Field(foreign_key="roles.id", max_length=36, index=True)
    full_name: str = Field(sa_column=Column(String(150), nullable=False))
    email: str = Field(sa_column=Column(String(255), unique=True, nullable=False, index=True))
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    phone: str | None = Field(default=None, sa_column=Column(String(30), nullable=True))
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
