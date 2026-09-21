from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChatMessageRead(BaseModel):
    id: str
    sender_type: str
    content: str
    intent: str | None
    confidence: float | None
    faq_id: str | None = None
    response_source: str | None = None
    created_at: datetime


class ConversationRead(BaseModel):
    id: str
    user_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    detected_intent: str | None = None
    last_faq_id: str | None = None
    category_id: str | None = None
    pending_question: str | None = None
    turn_count: int = 0
    last_confidence: float | None = None
    escalation_reason: str | None = None
    escalated_at: datetime | None = None
    converted_at: datetime | None = None
    last_activity_at: datetime | None = None
    messages: list[ChatMessageRead] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El mensaje no puede estar vacío")
        return value


class BotMessageResponse(BaseModel):
    user_message: ChatMessageRead
    bot_message: ChatMessageRead
    resolved: bool
    offers_ticket: bool
    confidence: float = 0.0
    faq_id: str | None = None
    source_category: str | None = None
    requires_clarification: bool = False
    clarification_options: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    conversation_status: str = "ACTIVE"
    response_source: str = "DETERMINISTIC"
    sources: list[dict[str, str | None]] = Field(default_factory=list)


class ConversationActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return " ".join(value.split()).strip() if value else None


class ChatFeedbackCreate(BaseModel):
    is_helpful: bool
    escalation_accepted: bool | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return " ".join(value.split()).strip() if value else None


class ChatFeedbackRead(BaseModel):
    id: str
    is_helpful: bool
    escalation_accepted: bool | None = None
    created_at: datetime


class LinkConversationResponse(BaseModel):
    id: str
    user_id: str
    status: str
