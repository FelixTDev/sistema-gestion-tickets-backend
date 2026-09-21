from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.adjuntos.models.attachment import AttachmentStatus


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticket_id: str
    comment_id: str | None
    uploaded_by_user_id: str
    original_filename: str
    mime_type_declared: str
    mime_type_detected: str
    file_size: int
    sha256: str
    status: AttachmentStatus
    created_at: datetime
    deleted_at: datetime | None
