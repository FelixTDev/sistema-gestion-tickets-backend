from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.reportes.schemas.report import (
    CategoryReport,
    PriorityReport,
    ResolutionTimeReport,
    StatusReport,
    SummaryReport,
)
from app.modules.reportes.services.report_service import ReportService
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus

router = APIRouter(prefix="/reports", tags=["reports"])


def get_report_service() -> ReportService:
    return ReportService()


ReportServiceDependency = Annotated[ReportService, Depends(get_report_service)]
SessionDependency = Annotated[Session, Depends(get_session)]


def filters(
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    category_id: str | None = None,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
) -> dict[str, object]:
    return {
        "from_date": from_date,
        "to_date": to_date,
        "category_id": category_id,
        "status": status,
        "priority": priority,
    }


FiltersDependency = Annotated[dict[str, object], Depends(filters)]


@router.get("/summary", response_model=SummaryReport)
def summary(
    session: SessionDependency,
    current_user: CurrentUser,
    service: ReportServiceDependency,
    report_filters: FiltersDependency,
) -> SummaryReport:
    return service.summary(session, current_user, **report_filters)


@router.get("/by-status", response_model=StatusReport)
def by_status(
    session: SessionDependency,
    current_user: CurrentUser,
    service: ReportServiceDependency,
    report_filters: FiltersDependency,
) -> StatusReport:
    return service.by_status(session, current_user, **report_filters)


@router.get("/by-category", response_model=CategoryReport)
def by_category(
    session: SessionDependency,
    current_user: CurrentUser,
    service: ReportServiceDependency,
    report_filters: FiltersDependency,
) -> CategoryReport:
    return service.by_category(session, current_user, **report_filters)


@router.get("/by-priority", response_model=PriorityReport)
def by_priority(
    session: SessionDependency,
    current_user: CurrentUser,
    service: ReportServiceDependency,
    report_filters: FiltersDependency,
) -> PriorityReport:
    return service.by_priority(session, current_user, **report_filters)


@router.get("/resolution-time", response_model=ResolutionTimeReport)
def resolution_time(
    session: SessionDependency,
    current_user: CurrentUser,
    service: ReportServiceDependency,
    report_filters: FiltersDependency,
) -> ResolutionTimeReport:
    return service.resolution_time(session, current_user, **report_filters)
