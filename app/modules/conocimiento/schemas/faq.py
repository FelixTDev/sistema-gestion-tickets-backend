from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FAQRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category_id: str
    question: str
    answer: str
    keywords: str
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class FAQCreate(BaseModel):
    category_id: str
    question: str = Field(min_length=3, max_length=2000)
    answer: str = Field(min_length=1, max_length=10000)
    keywords: str = Field(min_length=1, max_length=2000)


class FAQUpdate(BaseModel):
    category_id: str | None = None
    question: str | None = Field(default=None, min_length=3, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=10000)
    keywords: str | None = Field(default=None, min_length=1, max_length=2000)


class ActiveStatusUpdate(BaseModel):
    is_active: bool
