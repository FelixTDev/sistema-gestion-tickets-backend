from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.tickets.models.ticket import (
    TicketPriority,
    TicketSource,
    TicketStatus,
)


class ReportName(StrEnum):
    SUMMARY = "summary"
    BY_STATUS = "by-status"
    BY_PRIORITY = "by-priority"
    BY_CATEGORY = "by-category"
    BY_SOURCE = "by-source"
    BY_ADVISOR = "by-advisor"
    CREATED_TICKETS = "created-tickets"
    RESOLVED_TICKETS = "resolved-tickets"
    FIRST_RESPONSE_TIME = "first-response-time"
    RESOLUTION_TIME = "resolution-time"
    SLA_COMPLIANCE = "sla-compliance"
    CONVERSATIONS = "conversations"
    FAQ_UTILITY = "faq-utility"
    OPERATIONAL_ACTIVITY = "operational-activity"


class ReportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ReportExportFilters(BaseModel):
    from_date: datetime | None = None
    to_date: datetime | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    category_id: str | None = Field(default=None, min_length=1, max_length=36)
    source: TicketSource | None = None
    advisor_id: str | None = Field(default=None, min_length=1, max_length=36)
    client_id: str | None = Field(default=None, min_length=1, max_length=36)
    sla_compliant: bool | None = None
    search: str | None = Field(default=None, min_length=1, max_length=120)
    limit: int = Field(default=10_000, ge=1, le=10_000)


class ReportItemStatus(BaseModel):
    status: TicketStatus
    count: int


class ReportItemCategory(BaseModel):
    category_id: str
    category_name: str
    count: int


class ReportItemPriority(BaseModel):
    priority: TicketPriority
    count: int


class SummaryReport(BaseModel):
    total_tickets: int
    new_tickets: int
    assigned_tickets: int
    in_process_tickets: int
    pending_client_tickets: int
    resolved_tickets: int
    closed_tickets: int
    cancelled_tickets: int
    average_resolution_time_hours: float


class StatusReport(BaseModel):
    items: list[ReportItemStatus]


class CategoryReport(BaseModel):
    items: list[ReportItemCategory]


class PriorityReport(BaseModel):
    items: list[ReportItemPriority]


class ResolutionTimeReport(BaseModel):
    resolved_tickets: int
    average_resolution_time_hours: float
