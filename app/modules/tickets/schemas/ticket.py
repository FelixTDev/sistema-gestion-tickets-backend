from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.tickets.models.ticket import TicketPriority, TicketSource, TicketStatus


class TicketCreate(BaseModel):
    category_id: str
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5, max_length=10000)
    priority: TicketPriority = TicketPriority.MEDIA


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tracking_code: str
    client_id: str
    conversation_id: str | None
    category_id: str
    subject: str
    description: str
    priority: TicketPriority
    status: TicketStatus
    source: TicketSource
    assigned_advisor_id: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    assigned_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None


class TicketPage(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    items: list[TicketRead]


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El comentario no puede estar vacío")
        return value.strip()


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    author_id: str
    content: str
    created_at: datetime


class StatusChange(BaseModel):
    status: TicketStatus
    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El motivo no puede estar vacío")
        return value.strip()


class AssignmentCreate(BaseModel):
    advisor_id: str
    expected_version: int | None = Field(default=None, ge=1)


class HistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    actor_id: str | None
    action: str
    old_value: str | None
    new_value: str | None
    description: str
    created_at: datetime
