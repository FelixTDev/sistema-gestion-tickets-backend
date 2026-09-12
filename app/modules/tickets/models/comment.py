from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class TicketComment(SQLModel, table=True):
    __tablename__ = "ticket_comments"

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    ticket_id: str = Field(foreign_key="tickets.id", max_length=36)
    author_id: str = Field(foreign_key="users.id", max_length=36)
    content: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
