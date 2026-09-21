from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.notificaciones.models.notification import NotificationType

MetadataValue = str | int | float | bool | None
_FORBIDDEN_METADATA_PARTS = (
    "password",
    "hash",
    "token",
    "secret",
    "authorization",
    "cookie",
    "credential",
    "api_key",
)


class NotificationMetadata(BaseModel):
    values: dict[str, MetadataValue] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values(
        cls, value: dict[str, MetadataValue]
    ) -> dict[str, MetadataValue]:
        if len(value) > 20:
            raise ValueError("La metadata no puede contener más de 20 claves")
        for key, item in value.items():
            normalized_key = key.lower().strip()
            if not normalized_key or len(normalized_key) > 64:
                raise ValueError("Las claves de metadata no son válidas")
            if any(part in normalized_key for part in _FORBIDDEN_METADATA_PARTS):
                raise ValueError("La metadata contiene un campo sensible")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError("Los valores de metadata son demasiado largos")
        return value


def validate_metadata(value: dict[str, object] | None) -> dict[str, MetadataValue]:
    return NotificationMetadata(values=dict(value or {})).values


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    recipient_user_id: str
    type: NotificationType = Field(
        validation_alias="notification_type", serialization_alias="type"
    )
    title: str
    message: str
    related_ticket_id: str | None = None
    related_conversation_id: str | None = None
    is_read: bool
    created_at: datetime
    read_at: datetime | None = None
    metadata: dict[str, MetadataValue] = Field(
        default_factory=dict,
        validation_alias="metadata_json",
        serialization_alias="metadata",
    )


class NotificationPage(BaseModel):
    page: Annotated[int, Field(ge=1)]
    page_size: Annotated[int, Field(ge=1, le=100)]
    total: int
    total_pages: int
    items: list[NotificationRead]


class UnreadCountRead(BaseModel):
    unread_count: int


class ReadAllResponse(BaseModel):
    updated_count: int
