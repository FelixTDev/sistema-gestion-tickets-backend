from pydantic import BaseModel

from app.modules.tickets.models.ticket import TicketPriority, TicketStatus


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
