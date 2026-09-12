from uuid import uuid4

from sqlalchemy import Column, String, Text
from sqlmodel import Field, SQLModel


class TicketCategory(SQLModel, table=True):
    __tablename__ = "ticket_categories"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    name: str = Field(sa_column=Column(String(80), unique=True, nullable=False))
    description: str = Field(sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=True, nullable=False)
