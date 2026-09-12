from datetime import datetime

from sqlmodel import Session, select

from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.ticket import Ticket, TicketPriority, TicketStatus


class ReportRepository:
    def filtered_tickets(
        self,
        session: Session,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        category_id: str | None = None,
        status: TicketStatus | None = None,
        priority: TicketPriority | None = None,
    ) -> list[Ticket]:
        statement = select(Ticket)
        if from_date is not None:
            statement = statement.where(Ticket.created_at >= from_date)
        if to_date is not None:
            statement = statement.where(Ticket.created_at <= to_date)
        if category_id is not None:
            statement = statement.where(Ticket.category_id == category_id)
        if status is not None:
            statement = statement.where(Ticket.status == status)
        if priority is not None:
            statement = statement.where(Ticket.priority == priority)
        return list(session.exec(statement).all())

    def categories(
        self, session: Session, category_ids: set[str]
    ) -> dict[str, TicketCategory]:
        if not category_ids:
            return {}
        rows = session.exec(
            select(TicketCategory).where(TicketCategory.id.in_(category_ids))
        ).all()
        return {category.id: category for category in rows}
