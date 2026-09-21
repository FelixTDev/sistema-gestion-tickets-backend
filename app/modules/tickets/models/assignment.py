from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class TicketAssignment(SQLModel, table=True):
    __tablename__ = "ticket_assignments"
    __table_args__ = (
        Index(
            "ix_ticket_assignments_ticket_active",
            "ticket_id",
            "unassigned_at",
            "assigned_at",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    ticket_id: str = Field(foreign_key="tickets.id", max_length=36)
    advisor_id: str = Field(foreign_key="users.id", max_length=36)
    assigned_by: str = Field(foreign_key="users.id", max_length=36)
    assigned_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    unassigned_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
