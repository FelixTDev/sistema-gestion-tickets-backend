from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChatMessageRead(BaseModel):
    id: str
    sender_type: str
    content: str
    intent: str | None
    confidence: float | None
    created_at: datetime


class ConversationRead(BaseModel):
    id: str
    user_id: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    messages: list[ChatMessageRead] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

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


class LinkConversationResponse(BaseModel):
    id: str
    user_id: str
    status: str
