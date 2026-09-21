from collections.abc import Iterator
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.adjuntos.schemas.attachment import AttachmentRead
from app.modules.adjuntos.services.attachment_service import (
    AttachmentInputError,
    AttachmentMimeError,
    AttachmentService,
    AttachmentTooLargeError,
)

router = APIRouter(tags=["attachments"])


def get_attachment_service() -> AttachmentService:
    return AttachmentService()


AttachmentServiceDependency = Annotated[
    AttachmentService, Depends(get_attachment_service)
]
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/tickets/{ticket_id}/attachments",
    response_model=AttachmentRead,
    status_code=201,
)
def upload_attachment(
    ticket_id: str,
    file: Annotated[UploadFile, File(...)],
    session: SessionDependency,
    current_user: CurrentUser,
    service: AttachmentServiceDependency,
    comment_id: Annotated[str | None, Form()] = None,
) -> AttachmentRead:
    try:
        attachment = service.upload(
            session, ticket_id, current_user, file, comment_id=comment_id
        )
    except AttachmentTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except AttachmentMimeError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error
    except AttachmentInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AttachmentRead.model_validate(attachment)


@router.get("/tickets/{ticket_id}/attachments", response_model=list[AttachmentRead])
def list_attachments(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: AttachmentServiceDependency,
) -> list[AttachmentRead]:
    return [
        AttachmentRead.model_validate(attachment)
        for attachment in service.list_for_ticket(session, ticket_id, current_user)
    ]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: AttachmentServiceDependency,
) -> StreamingResponse:
    attachment, stream = service.download(session, attachment_id, current_user)
    safe_filename = attachment.original_filename.replace('"', "_")
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        _stream_chunks(stream),
        media_type=attachment.mime_type_detected,
        headers=headers,
        background=BackgroundTask(stream.close),
    )


@router.delete("/attachments/{attachment_id}", response_model=AttachmentRead)
def delete_attachment(
    attachment_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: AttachmentServiceDependency,
) -> AttachmentRead:
    return AttachmentRead.model_validate(
        service.delete(session, attachment_id, current_user)
    )


def _stream_chunks(stream: BinaryIO) -> Iterator[bytes]:
    while chunk := stream.read(64 * 1024):
        yield chunk
