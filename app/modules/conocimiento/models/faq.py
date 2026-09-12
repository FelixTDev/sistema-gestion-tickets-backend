from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class FAQ(SQLModel, table=True):
    __tablename__ = "faqs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True, max_length=36)
    category_id: str = Field(foreign_key="ticket_categories.id", max_length=36, index=True)
    question: str = Field(sa_column=Column(Text, nullable=False))
    answer: str = Field(sa_column=Column(Text, nullable=False))
    keywords: str = Field(sa_column=Column(Text, nullable=False, default=""))
    is_active: bool = Field(default=True, nullable=False)
    created_by: str = Field(foreign_key="users.id", max_length=36, index=True)
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
