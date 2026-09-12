from uuid import uuid4

from sqlalchemy import Column, String, Text
from sqlmodel import Field, SQLModel


class Role(SQLModel, table=True):
    __tablename__ = "roles"

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    name: str = Field(sa_column=Column(String(30), unique=True, nullable=False))
    description: str = Field(sa_column=Column(Text, nullable=False))
