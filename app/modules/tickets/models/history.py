from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class TicketHistory(SQLModel, table=True):
    __tablename__ = "ticket_history"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    ticket_id: str = Field(foreign_key="tickets.id", max_length=36, index=True)
    actor_id: str | None = Field(default=None, foreign_key="users.id", max_length=36, index=True)
    action: str = Field(sa_column=Column(String(50), nullable=False))
    old_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    new_value: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    description: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
