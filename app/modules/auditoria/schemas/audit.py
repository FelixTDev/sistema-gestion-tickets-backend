from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    action: str
    actor_user_id: str | None
    actor_role: str | None
    resource_type: str
    resource_id: str | None
    target_user_id: str | None
    occurred_at: datetime
    success: bool
    error_code: str | None
    before_data: dict[str, object] | None
    after_data: dict[str, object] | None
    metadata: dict[str, object] | None
    request_id: str | None
    correlation_id: str | None

    @classmethod
    def from_model(cls, audit: object) -> "AuditRead":
        return cls(
            id=audit.id,
            event_type=audit.event_type,
            action=audit.action,
            actor_user_id=audit.actor_user_id,
            actor_role=audit.actor_role,
            resource_type=audit.resource_type,
            resource_id=audit.resource_id,
            target_user_id=audit.target_user_id,
            occurred_at=audit.occurred_at,
            success=audit.success,
            error_code=audit.error_code,
            before_data=audit.before_data,
            after_data=audit.after_data,
            metadata=audit.metadata_json,
            request_id=audit.request_id,
            correlation_id=audit.correlation_id,
        )


class AuditPage(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    items: list[AuditRead]
