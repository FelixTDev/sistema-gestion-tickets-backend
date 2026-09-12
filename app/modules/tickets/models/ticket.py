from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Column, DateTime, Enum, String, Text
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class TicketPriority(StrEnum):
    BAJA = "BAJA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"
    URGENTE = "URGENTE"


class TicketStatus(StrEnum):
    NUEVO = "NUEVO"
    ASIGNADO = "ASIGNADO"
    EN_PROCESO = "EN_PROCESO"
    PENDIENTE_CLIENTE = "PENDIENTE_CLIENTE"
    RESUELTO = "RESUELTO"
    CERRADO = "CERRADO"
    CANCELADO = "CANCELADO"


class TicketSource(StrEnum):
    CHATBOT = "CHATBOT"
    MANUAL = "MANUAL"


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    tracking_code: str = Field(
        sa_column=Column(String(30), unique=True, nullable=False)
    )
    client_id: str = Field(foreign_key="users.id", max_length=36)
    conversation_id: str | None = Field(
        default=None, foreign_key="conversations.id", max_length=36, unique=True
    )
    category_id: str = Field(foreign_key="ticket_categories.id", max_length=36)
    subject: str = Field(sa_column=Column(String(200), nullable=False))
    description: str = Field(sa_column=Column(Text, nullable=False))
    priority: TicketPriority = Field(
        default=TicketPriority.MEDIA,
        sa_column=Column(Enum(TicketPriority), nullable=False),
    )
    status: TicketStatus = Field(
        default=TicketStatus.NUEVO,
        sa_column=Column(Enum(TicketStatus), nullable=False),
    )
    source: TicketSource = Field(
        default=TicketSource.MANUAL,
        sa_column=Column(Enum(TicketSource), nullable=False),
    )
    assigned_advisor_id: str | None = Field(
        default=None, foreign_key="users.id", max_length=36
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    assigned_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    closed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    cancelled_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
