import json
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.conocimiento.models.faq import FAQ

FAQStatus = FAQ.Status


def _terms(value: object, *, allow_none: bool = False) -> list[str] | None:
    if value is None and allow_none:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [term for term in value.split(",") if term.strip()]
    if not isinstance(value, list):
        raise ValueError("Debe enviarse una lista de términos")
    cleaned: list[str] = []
    seen: set[str] = set()
    for term in value:
        if not isinstance(term, str):
            raise ValueError("Cada término debe ser texto")
        normalized = term.strip()
        key = normalized.casefold()
        if not normalized or len(normalized) > 80:
            raise ValueError("Los términos deben tener entre 1 y 80 caracteres")
        if key not in seen:
            seen.add(key)
            cleaned.append(normalized)
    if len(cleaned) > 20:
        raise ValueError("No se permiten más de 20 términos")
    return cleaned


class FAQRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category_id: str
    title: str
    question: str
    answer: str
    summary: str
    keywords: str
    tags: list[str]
    synonyms: list[str]
    intent: str | None
    source_url: str | None = None
    source_verified_at: datetime | None = None
    status: FAQStatus
    priority: int
    display_order: int
    version: int
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None

    @field_validator("tags", "synonyms", mode="before")
    @classmethod
    def parse_terms(cls, value: object) -> list[str]:
        return _terms(value) or []


class FAQAdminRead(FAQRead):
    updated_by: str | None
    unpublished_at: datetime | None


class FAQPage(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    items: list[FAQRead]


class FAQAdminPage(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    items: list[FAQAdminRead]


class FAQCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str
    title: str | None = Field(default=None, min_length=3, max_length=200)
    question: str = Field(min_length=3, max_length=2000)
    answer: str = Field(min_length=1, max_length=10000)
    summary: str | None = Field(default=None, max_length=1000)
    keywords: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    intent: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=2048)
    source_verified_at: datetime | None = None
    priority: int = Field(default=0, ge=0, le=100)
    display_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("tags", "synonyms")
    @classmethod
    def validate_terms(cls, value: list[str]) -> list[str]:
        return _terms(value) or []

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        return _official_source_url(value)

    @model_validator(mode="after")
    def validate_content(self) -> "FAQCreate":
        if not self.question.strip() or not self.answer.strip():
            raise ValueError("La pregunta y la respuesta no pueden estar vacías")
        if self.source_verified_at is not None and self.source_url is None:
            raise ValueError("La fecha de verificación requiere una fuente oficial")
        _validate_source_date(self.source_verified_at)
        return self


class FAQUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: str | None = None
    title: str | None = Field(default=None, min_length=3, max_length=200)
    question: str | None = Field(default=None, min_length=3, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=10000)
    summary: str | None = Field(default=None, max_length=1000)
    keywords: str | None = Field(default=None, min_length=1, max_length=2000)
    tags: list[str] | None = None
    synonyms: list[str] | None = None
    intent: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=2048)
    source_verified_at: datetime | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    display_order: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("tags", "synonyms")
    @classmethod
    def validate_terms(cls, value: list[str] | None) -> list[str] | None:
        return _terms(value, allow_none=True)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        return _official_source_url(value)

    @model_validator(mode="after")
    def require_changes(self) -> "FAQUpdate":
        if not self.model_fields_set:
            raise ValueError("Debe enviarse al menos un campo para actualizar")
        if (
            "source_url" in self.model_fields_set
            and self.source_url is None
            and self.source_verified_at is not None
        ):
            raise ValueError("La fecha de verificación requiere una fuente oficial")
        _validate_source_date(self.source_verified_at)
        return self


class WorkflowStatusUpdate(BaseModel):
    status: FAQStatus


class ActiveStatusUpdate(BaseModel):
    is_active: bool


class FAQVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    faq_id: str
    version: int
    category_id: str
    title: str
    question: str
    answer: str
    summary: str
    keywords: str
    tags: list[str]
    synonyms: list[str]
    intent: str | None
    source_url: str | None = None
    source_verified_at: datetime | None = None
    status: FAQStatus
    priority: int
    display_order: int
    is_active: bool
    published_at: datetime | None
    unpublished_at: datetime | None
    changed_by: str
    action: str
    changed_at: datetime

    @field_validator("tags", "synonyms", mode="before")
    @classmethod
    def parse_terms(cls, value: object) -> list[str]:
        return _terms(value) or []


class FAQFeedbackCreate(BaseModel):
    is_helpful: bool
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        return cleaned or None


class FAQFeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_helpful: bool
    created_at: datetime


class FAQUtilityMetrics(BaseModel):
    total_feedback: int
    helpful: int
    not_helpful: int
    usefulness_rate: float


def _official_source_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "La fuente debe ser una URL HTTPS oficial de Banco GNB"
        ) from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.bancognb.com.pe", "bancognb.com.pe"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or any(character.isspace() for character in value)
    ):
        raise ValueError("La fuente debe ser una URL HTTPS oficial de Banco GNB")
    return value


def _validate_source_date(value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError("La fecha de verificación debe incluir zona horaria")
