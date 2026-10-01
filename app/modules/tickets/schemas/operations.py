from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


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


class TicketReleaseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El motivo no puede estar vacío")
        return value.strip()
