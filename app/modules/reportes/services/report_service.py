from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.reportes.repositories.report_repository import ReportRepository
from app.modules.reportes.schemas.report import (
    CategoryReport,
    PriorityReport,
    ReportItemCategory,
    ReportItemPriority,
    ReportItemStatus,
    ResolutionTimeReport,
    StatusReport,
    SummaryReport,
)
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus


class ReportService:
    def __init__(self, repository: ReportRepository | None = None) -> None:
        self.repository = repository or ReportRepository()

    def summary(
        self, session: Session, actor: AuthenticatedUser, **filters: object
    ) -> SummaryReport:
        tickets = self._tickets(session, actor, filters)
        counts = {
            status: sum(ticket.status == status for ticket in tickets)
            for status in TicketStatus
        }
        return SummaryReport(
            total_tickets=len(tickets),
            new_tickets=counts[TicketStatus.NUEVO],
            assigned_tickets=counts[TicketStatus.ASIGNADO],
            in_process_tickets=counts[TicketStatus.EN_PROCESO],
            pending_client_tickets=counts[TicketStatus.PENDIENTE_CLIENTE],
            resolved_tickets=counts[TicketStatus.RESUELTO],
            closed_tickets=counts[TicketStatus.CERRADO],
            cancelled_tickets=counts[TicketStatus.CANCELADO],
            average_resolution_time_hours=self._average_hours(tickets),
        )

    def by_status(
        self, session: Session, actor: AuthenticatedUser, **filters: object
    ) -> StatusReport:
        tickets = self._tickets(session, actor, filters)
        items = [
            ReportItemStatus(
                status=state, count=sum(ticket.status == state for ticket in tickets)
            )
            for state in TicketStatus
            if any(ticket.status == state for ticket in tickets)
        ]
        return StatusReport(items=items)

    def by_category(
        self, session: Session, actor: AuthenticatedUser, **filters: object
    ) -> CategoryReport:
        tickets = self._tickets(session, actor, filters)
        categories = self.repository.categories(
            session, {ticket.category_id for ticket in tickets}
        )
        counts: dict[str, int] = {}
        for ticket in tickets:
            counts[ticket.category_id] = counts.get(ticket.category_id, 0) + 1
        return CategoryReport(
            items=[
                ReportItemCategory(
                    category_id=category_id,
                    category_name=categories[category_id].name,
                    count=count,
                )
                for category_id, count in counts.items()
                if category_id in categories
            ]
        )

    def by_priority(
        self, session: Session, actor: AuthenticatedUser, **filters: object
    ) -> PriorityReport:
        tickets = self._tickets(session, actor, filters)
        items = [
            ReportItemPriority(
                priority=priority,
                count=sum(ticket.priority == priority for ticket in tickets),
            )
            for priority in TicketPriority
            if any(ticket.priority == priority for ticket in tickets)
        ]
        return PriorityReport(items=items)

    def resolution_time(
        self, session: Session, actor: AuthenticatedUser, **filters: object
    ) -> ResolutionTimeReport:
        tickets = self._tickets(session, actor, filters)
        return ResolutionTimeReport(
            resolved_tickets=sum(ticket.resolved_at is not None for ticket in tickets),
            average_resolution_time_hours=self._average_hours(tickets),
        )

    def _tickets(
        self, session: Session, actor: AuthenticatedUser, filters: dict[str, object]
    ):
        if actor.role != "SUPERVISOR":
            raise HTTPException(
                status_code=403, detail="Solo un supervisor puede consultar reportes"
            )
        from_date = filters.get("from_date")
        to_date = filters.get("to_date")
        if from_date is not None and to_date is not None and from_date > to_date:
            raise HTTPException(
                status_code=422, detail="El rango de fechas no es válido"
            )
        return self.repository.filtered_tickets(session, **filters)

    @staticmethod
    def _average_hours(tickets: list) -> float:
        durations = [
            (ticket.resolved_at - ticket.created_at).total_seconds() / 3600
            for ticket in tickets
            if ticket.resolved_at is not None
        ]
        return round(sum(durations) / len(durations), 2) if durations else 0.0
