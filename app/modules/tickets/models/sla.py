from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from app.modules.tickets.models.ticket import TicketPriority, TicketSource
from app.modules.usuarios.models.user import utc_now


class SlaStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    BREACHED = "BREACHED"
    CANCELLED = "CANCELLED"


class SlaPolicy(SQLModel, table=True):
    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("policy_key", name="uq_sla_policies_policy_key"),
        Index(
            "ix_sla_policies_scope",
            "is_active",
            "priority",
            "category_id",
            "source",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    policy_key: str = Field(sa_column=Column(String(220), nullable=False))
    priority: TicketPriority | None = Field(
        default=None, sa_column=Column(Enum(TicketPriority), nullable=True)
    )
    category_id: str | None = Field(
        default=None, foreign_key="ticket_categories.id", max_length=36
    )
    source: TicketSource | None = Field(
        default=None, sa_column=Column(Enum(TicketSource), nullable=True)
    )
    first_response_seconds: int = Field(sa_column=Column(Integer, nullable=False))
    resolution_seconds: int = Field(sa_column=Column(Integer, nullable=False))
    warning_seconds: int = Field(sa_column=Column(Integer, nullable=False))
    timezone_name: str = Field(
        default="UTC", sa_column=Column(String(64), nullable=False)
    )
    calendar_name: str = Field(
        default="24x7", sa_column=Column(String(40), nullable=False)
    )
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TicketSla(SQLModel, table=True):
    __tablename__ = "ticket_slas"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_slas_ticket_id"),
        Index(
            "ix_ticket_slas_status_resolution_due",
            "status",
            "resolution_due_at",
        ),
        Index(
            "ix_ticket_slas_status_first_response_due",
            "status",
            "first_response_due_at",
        ),
        Index("ix_ticket_slas_ticket_id", "ticket_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    ticket_id: str = Field(foreign_key="tickets.id", max_length=36)
    policy_id: str = Field(foreign_key="sla_policies.id", max_length=36)
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    first_response_due_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    resolution_due_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    first_responded_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    first_response_within_sla: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    first_response_warning_sent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolution_warning_sent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    first_response_breached_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    paused_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    total_paused_seconds: int = Field(
        default=0, sa_column=Column(Integer, nullable=False)
    )
    status: SlaStatus = Field(
        default=SlaStatus.ACTIVE,
        sa_column=Column(Enum(SlaStatus), nullable=False),
    )
    warning_sent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    warning_stage: str | None = Field(
        default=None, sa_column=Column(String(30), nullable=True)
    )
    breached_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolution_breached_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completed_within_sla: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    reopen_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class SlaPolicyHistory(SQLModel, table=True):
    __tablename__ = "sla_policy_history"
    __table_args__ = (
        Index("ix_sla_policy_history_policy_created", "policy_id", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    policy_id: str = Field(foreign_key="sla_policies.id", max_length=36)
    actor_id: str = Field(foreign_key="users.id", max_length=36)
    action: str = Field(sa_column=Column(String(30), nullable=False))
    old_values: dict[str, object] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    new_values: dict[str, object] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
