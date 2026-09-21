from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.tickets.models.ticket import TicketPriority, TicketSource, TicketStatus
from app.modules.tickets.schemas.operations import OperationalQueue, TicketActionRequest
from app.modules.tickets.schemas.ticket import (
    AssignmentCreate,
    CommentCreate,
    CommentRead,
    HistoryRead,
    ReasonRequest,
    StatusChange,
    TicketCreate,
    TicketPage,
    TicketRead,
)
from app.modules.tickets.services.ticket_service import TicketService
from app.shared.pagination import PaginationResult

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


@router.get("/mine", response_model=list[TicketRead] | TicketPage)
def list_my_tickets(
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    category_id: Annotated[str | None, Query(max_length=36)] = None,
    priority: Annotated[TicketPriority | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    source: Annotated[TicketSource | None, Query()] = None,
    assigned_advisor_id: Annotated[str | None, Query(max_length=36)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[TicketRead] | TicketPage:
    result = service.list_mine(
        session,
        current_user,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        category_id=category_id,
        priority=priority,
        created_from=created_from,
        created_to=created_to,
        source=source,
        assigned_advisor_id=assigned_advisor_id,
        search=search,
        updated_from=updated_from,
        updated_to=updated_to,
    )
    return _ticket_list_response(result)


@router.get("", response_model=list[TicketRead] | TicketPage)
def list_tickets(
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    category_id: Annotated[str | None, Query(max_length=36)] = None,
    priority: Annotated[TicketPriority | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    source: Annotated[TicketSource | None, Query()] = None,
    client_id: Annotated[str | None, Query(max_length=36)] = None,
    assigned_advisor_id: Annotated[str | None, Query(max_length=36)] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[TicketRead] | TicketPage:
    result = service.list_global(
        session,
        current_user,
        status_filter,
        category_id,
        priority,
        created_from,
        created_to,
        page=page,
        page_size=page_size,
        source=source,
        client_id=client_id,
        assigned_advisor_id=assigned_advisor_id,
        search=search,
        updated_from=updated_from,
        updated_to=updated_to,
    )
    return _ticket_list_response(result)


def _ticket_list_response(
    result: list | PaginationResult,
) -> list[TicketRead] | TicketPage:
    if isinstance(result, PaginationResult):
        return TicketPage(
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            total_pages=result.total_pages,
            items=result.items,
        )
    return result


@router.get("/operations", response_model=TicketPage)
def list_operational_tickets(
    queue: Annotated[OperationalQueue, Query()],
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    status_filter: Annotated[TicketStatus | None, Query(alias="status")] = None,
    category_id: Annotated[str | None, Query(max_length=36)] = None,
    priority: Annotated[TicketPriority | None, Query()] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
    source: Annotated[TicketSource | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    advisor_id: Annotated[str | None, Query(max_length=36)] = None,
    recent_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TicketPage:
    result = service.list_operational(
        session,
        current_user,
        queue,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        category_id=category_id,
        priority=priority,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        source=source,
        search=search,
        advisor_id=advisor_id,
        recent_hours=recent_hours,
    )
    return _ticket_list_response(result)


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


@router.post("/{ticket_id}/take", response_model=TicketRead)
def take_ticket(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    data: TicketActionRequest | None = None,
) -> TicketRead:
    return service.take(
        session,
        ticket_id,
        current_user,
        data.expected_version if data is not None else None,
    )


@router.post("/{ticket_id}/release", response_model=TicketRead)
def release_ticket(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: TicketServiceDependency,
    data: TicketActionRequest | None = None,
) -> TicketRead:
    return service.release(
        session,
        ticket_id,
        current_user,
        data.expected_version if data is not None else None,
    )


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
