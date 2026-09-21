from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.notificaciones.schemas.notification import (
    NotificationPage,
    NotificationRead,
    ReadAllResponse,
    UnreadCountRead,
)
from app.modules.notificaciones.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notification_service() -> NotificationService:
    return NotificationService()


@router.get("", response_model=NotificationPage)
def list_notifications(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NotificationPage:
    result = service.list_for_user(session, current_user.user.id, page, page_size)
    return NotificationPage(
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_pages=result.total_pages,
        items=[NotificationRead.model_validate(item) for item in result.items],
    )


@router.get("/unread-count", response_model=UnreadCountRead)
def unread_count(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> UnreadCountRead:
    return UnreadCountRead(
        unread_count=service.unread_count(session, current_user.user.id)
    )


@router.post("/read-all", response_model=ReadAllResponse)
def read_all(
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> ReadAllResponse:
    return ReadAllResponse(
        updated_count=service.mark_all_read(session, current_user.user.id)
    )


@router.patch("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: str,
    current_user: CurrentUser,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> NotificationRead:
    notification = service.mark_read(session, notification_id, current_user.user.id)
    return NotificationRead.model_validate(notification)
