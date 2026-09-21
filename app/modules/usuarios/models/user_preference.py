from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class UserPreference(SQLModel, table=True):
    __tablename__ = "user_preferences"

    user_id: str = Field(
        primary_key=True,
        foreign_key="users.id",
        max_length=36,
    )
    in_app_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False)
    )
    email_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    assignment_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False)
    )
    status_change_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False)
    )
    comment_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False)
    )
    sla_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    preferred_language: str = Field(
        default="es", sa_column=Column(String(10), nullable=False)
    )
    timezone: str = Field(default="UTC", sa_column=Column(String(64), nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
