from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class AuthRateLimit(SQLModel, table=True):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (
        UniqueConstraint("action", "key_hash", name="uq_auth_rate_limit_key"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    action: str = Field(sa_column=Column(String(40), nullable=False))
    key_hash: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    window_started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    blocked_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
