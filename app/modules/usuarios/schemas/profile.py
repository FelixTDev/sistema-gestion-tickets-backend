from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_FALLBACK_TIMEZONES = {
    "America/Bogota",
    "America/Lima",
    "America/Los_Angeles",
    "America/New_York",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Europe/London",
    "Europe/Madrid",
}


def _normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = "".join(
        character for character in value.strip() if character not in " ()-"
    )
    if not normalized or not normalized.lstrip("+").isdigit():
        raise ValueError("El teléfono no tiene un formato válido")
    digits = normalized.lstrip("+")
    if not 7 <= len(digits) <= 15:
        raise ValueError("El teléfono no tiene un formato válido")
    return f"+{digits}" if normalized.startswith("+") else digits


def _validate_timezone(value: str) -> str:
    normalized = value.strip()
    if normalized == "UTC":
        return normalized
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as error:
        if normalized in _FALLBACK_TIMEZONES:
            return normalized
        raise ValueError("La zona horaria no es válida") from error
    return normalized


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    email: EmailStr
    phone: str | None
    role: str
    email_verified: bool
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("El nombre no puede estar vacío")
        return normalized

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return _normalize_phone(value)


class PreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    in_app_enabled: bool
    email_enabled: bool
    assignment_enabled: bool
    status_change_enabled: bool
    comment_enabled: bool
    sla_enabled: bool
    preferred_language: Literal["es", "en"]
    timezone: str
    security_events_enabled: Literal[True] = True


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
    assignment_enabled: bool | None = None
    status_change_enabled: bool | None = None
    comment_enabled: bool | None = None
    sla_enabled: bool | None = None
    preferred_language: Literal["es", "en"] | None = None
    timezone: str | None = None
    security_events_enabled: bool | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value) if value is not None else None

    @field_validator("security_events_enabled")
    @classmethod
    def keep_security_events_enabled(cls, value: bool | None) -> bool | None:
        if value is False:
            raise ValueError("Las notificaciones de seguridad no se pueden desactivar")
        return value
