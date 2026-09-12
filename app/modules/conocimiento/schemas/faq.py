from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FAQRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category_id: str
    question: str
    answer: str
    keywords: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
