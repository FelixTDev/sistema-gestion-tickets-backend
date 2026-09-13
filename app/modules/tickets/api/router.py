from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus
from app.modules.tickets.schemas.ticket import (
    AssignmentCreate,
    CommentCreate,
    CommentRead,
    HistoryRead,
    ReasonRequest,
    StatusChange,
    TicketCreate,
    TicketRead,
)
from app.modules.tickets.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


def get_ticket_service() -> TicketService:
    return TicketService()


TicketServiceDependency = Annotated[TicketService, Depends(get_ticket_service)]
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post("", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def create_ticket(
    data: TicketCreate,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.create_manual(session, data, current_user)


@router.get("/mine", response_model=list[TicketRead])
def list_my_tickets(
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> list[TicketRead]:
    return service.list_mine(session, current_user)


@router.get("", response_model=list[TicketRead])
def list_tickets(
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    category_id: str | None = None,
    priority: TicketPriority | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[TicketRead]:
    return service.list_global(
        session,
        current_user,
        status_filter,
        category_id,
        priority,
        created_from,
        created_to,
    )


@router.get("/{ticket_id}", response_model=TicketRead)
def get_ticket(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.get(session, ticket_id, current_user)


@router.post("/{ticket_id}/comments", response_model=CommentRead, status_code=201)
def add_comment(
    ticket_id: str,
    data: CommentCreate,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> CommentRead:
    return service.add_comment(session, ticket_id, data, current_user)


@router.get("/{ticket_id}/comments", response_model=list[CommentRead])
def get_comments(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> list[CommentRead]:
    return service.comments(session, ticket_id, current_user)


@router.post("/{ticket_id}/status", response_model=TicketRead)
def change_status(
    ticket_id: str,
    data: StatusChange,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.change_status(session, ticket_id, data, current_user)


@router.post("/{ticket_id}/assignments", response_model=TicketRead, status_code=201)
def assign_ticket(
    ticket_id: str,
    data: AssignmentCreate,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.assign(session, ticket_id, data, current_user)


@router.get("/{ticket_id}/history", response_model=list[HistoryRead])
def get_history(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> list[HistoryRead]:
    return service.history(session, ticket_id, current_user)


@router.post("/{ticket_id}/close", response_model=TicketRead)
def close_ticket(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.close(session, ticket_id, current_user)


@router.post("/{ticket_id}/reopen", response_model=TicketRead)
def reopen_ticket(
    ticket_id: str,
    data: ReasonRequest,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.reopen(session, ticket_id, data, current_user)


@router.post("/{ticket_id}/cancel", response_model=TicketRead)
def cancel_ticket(
    ticket_id: str,
    data: ReasonRequest,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
) -> TicketRead:
    return service.cancel(session, ticket_id, data, current_user)
