from enum import StrEnum

from pydantic import BaseModel, Field


class OperationalQueue(StrEnum):
    ASSIGNED_TO_ME = "assigned_to_me"
    UNASSIGNED = "unassigned"
    ASSIGNED_TO_ADVISOR = "assigned_to_advisor"
    SLA_SOON = "sla_soon"
    SLA_OVERDUE = "sla_overdue"
    PENDING_FIRST_RESPONSE = "pending_first_response"
    RECENTLY_UPDATED = "recently_updated"


class TicketActionRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
