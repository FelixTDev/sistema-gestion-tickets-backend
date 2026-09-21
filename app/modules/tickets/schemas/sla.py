from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.tickets.models.sla import SlaStatus
from app.modules.tickets.models.ticket import TicketPriority, TicketSource


def _validate_timezone(value: str) -> str:
    normalized = value.strip()
    if normalized == "UTC":
        return normalized
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("La zona horaria no es válida") from error
    return normalized


class SlaPolicyCreate(BaseModel):
    priority: TicketPriority | None = None
    category_id: str | None = Field(default=None, max_length=36)
    source: TicketSource | None = None
    first_response_seconds: int = Field(gt=0, le=31_536_000)
    resolution_seconds: int = Field(gt=0, le=31_536_000)
    warning_seconds: int = Field(ge=0, le=31_536_000)
    timezone_name: str = Field(default="UTC", min_length=1, max_length=64)
    calendar_name: str = Field(default="24x7", min_length=1, max_length=40)
    is_active: bool = True

    _timezone_validator = field_validator("timezone_name")(_validate_timezone)

    @field_validator("calendar_name")
    @classmethod
    def calendar_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El calendario no puede estar vacío")
        if value.strip() != "24x7":
            raise ValueError("Solo está disponible el calendario 24x7")
        return value.strip()


class SlaPolicyUpdate(BaseModel):
    priority: TicketPriority | None = None
    category_id: str | None = Field(default=None, max_length=36)
    source: TicketSource | None = None
    first_response_seconds: Annotated[int | None, Field(gt=0, le=31_536_000)] = None
    resolution_seconds: Annotated[int | None, Field(gt=0, le=31_536_000)] = None
    warning_seconds: Annotated[int | None, Field(ge=0, le=31_536_000)] = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=64)
    calendar_name: str | None = Field(default=None, min_length=1, max_length=40)
    is_active: bool | None = None

    _timezone_validator = field_validator("timezone_name")(_validate_timezone)

    @field_validator("calendar_name")
    @classmethod
    def calendar_not_blank(cls, value: str | None) -> str | None:
        if value is not None and value.strip() != "24x7":
            raise ValueError("Solo está disponible el calendario 24x7")
        return value.strip() if value is not None else None


class SlaPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    policy_key: str
    priority: TicketPriority | None
    category_id: str | None
    source: TicketSource | None
    first_response_seconds: int
    resolution_seconds: int
    warning_seconds: int
    timezone_name: str
    calendar_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SlaActionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El motivo no puede estar vacío")
        return value.strip()


class TicketSlaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    policy_id: str
    policy_priority: TicketPriority | None = None
    policy_category_id: str | None = None
    policy_source: TicketSource | None = None
    policy_timezone_name: str
    policy_calendar_name: str
    started_at: datetime
    first_response_due_at: datetime
    resolution_due_at: datetime
    first_responded_at: datetime | None
    first_response_within_sla: bool | None
    first_response_warning_sent_at: datetime | None
    resolution_warning_sent_at: datetime | None
    first_response_breached_at: datetime | None
    resolved_at: datetime | None
    paused_at: datetime | None
    total_paused_seconds: int
    status: SlaStatus
    warning_sent_at: datetime | None
    warning_stage: str | None
    breached_at: datetime | None
    resolution_breached_at: datetime | None
    completed_within_sla: bool | None
    reopen_count: int
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "started_at",
        "first_response_due_at",
        "resolution_due_at",
        "first_responded_at",
        "first_response_warning_sent_at",
        "resolution_warning_sent_at",
        "first_response_breached_at",
        "resolved_at",
        "paused_at",
        "warning_sent_at",
        "breached_at",
        "resolution_breached_at",
        "created_at",
        "updated_at",
        mode="before",
    )
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SlaEvaluationRead(BaseModel):
    evaluated: int = Field(ge=0)
    warnings: int = Field(ge=0)
    breaches: int = Field(ge=0)
