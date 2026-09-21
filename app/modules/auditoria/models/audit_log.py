from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog(SQLModel, table=True):
    """Registro global append-only. No se expone ninguna mutación HTTP."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_occurred_at", "occurred_at"),
        Index("ix_audit_logs_actor_occurred", "actor_user_id", "occurred_at"),
        Index(
            "ix_audit_logs_resource_occurred",
            "resource_type",
            "resource_id",
            "occurred_at",
        ),
        Index("ix_audit_logs_event_occurred", "event_type", "occurred_at"),
        Index("ix_audit_logs_action_occurred", "action", "occurred_at"),
        Index("ix_audit_logs_target_occurred", "target_user_id", "occurred_at"),
        Index("ix_audit_logs_success_occurred", "success", "occurred_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    event_type: str = Field(sa_column=Column(String(80), nullable=False))
    action: str = Field(sa_column=Column(String(80), nullable=False))
    actor_user_id: str | None = Field(
        default=None,
        sa_column=Column(String(36), ForeignKey("users.id"), nullable=True),
    )
    actor_role: str | None = Field(
        default=None, sa_column=Column(String(30), nullable=True)
    )
    resource_type: str = Field(sa_column=Column(String(50), nullable=False))
    resource_id: str | None = Field(
        default=None, sa_column=Column(String(128), nullable=True)
    )
    target_user_id: str | None = Field(
        default=None,
        sa_column=Column(String(36), ForeignKey("users.id"), nullable=True),
    )
    occurred_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    success: bool = Field(sa_column=Column(Boolean, nullable=False))
    error_code: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    before_data: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    after_data: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    metadata_json: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    request_id: str | None = Field(
        default=None, sa_column=Column(String(128), nullable=True)
    )
    correlation_id: str | None = Field(
        default=None, sa_column=Column(String(128), nullable=True)
    )
    ip_address: str | None = Field(
        default=None, sa_column=Column(String(45), nullable=True)
    )
    user_agent: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    dedupe_key: str | None = Field(
        default=None, sa_column=Column(String(255), unique=True, nullable=True)
    )
