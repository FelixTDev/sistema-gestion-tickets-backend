from datetime import datetime

from sqlalchemy import case, func, literal_column, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.modules.chatbot.models.conversation import Conversation
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.reportes.schemas.report import ReportExportFilters, ReportName
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.sla import TicketSla
from app.modules.tickets.models.ticket import (
    Ticket,
    TicketPriority,
    TicketStatus,
)
from app.modules.usuarios.models.user import User


class ExportLimitExceeded(Exception):
    def __init__(self, total: int, limit: int) -> None:
        self.total = total
        self.limit = limit
        super().__init__(f"La exportación contiene {total} filas; el límite es {limit}")


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

    def export_rows(
        self,
        session: Session,
        report_name: ReportName,
        filters: ReportExportFilters,
    ) -> list[dict[str, object]]:
        if report_name == ReportName.SUMMARY:
            return [self._summary(session, filters)]
        if report_name == ReportName.BY_STATUS:
            return self._by_status(session, filters)
        if report_name == ReportName.BY_PRIORITY:
            return self._by_priority(session, filters)
        if report_name == ReportName.BY_CATEGORY:
            return self._by_category(session, filters)
        if report_name == ReportName.BY_SOURCE:
            return self._by_source(session, filters)
        if report_name == ReportName.BY_ADVISOR:
            return self._by_advisor(session, filters)
        if report_name == ReportName.CREATED_TICKETS:
            return self._detail_rows(session, filters, Ticket.created_at)
        if report_name == ReportName.RESOLVED_TICKETS:
            return self._detail_rows(
                session, filters, Ticket.resolved_at, require_resolved=True
            )
        if report_name == ReportName.FIRST_RESPONSE_TIME:
            return self._detail_rows(
                session,
                filters,
                TicketSla.first_responded_at,
                require_first_response=True,
            )
        if report_name == ReportName.RESOLUTION_TIME:
            return self._detail_rows(
                session, filters, Ticket.resolved_at, require_resolved=True
            )
        if report_name == ReportName.SLA_COMPLIANCE:
            return self._sla_rows(session, filters)
        if report_name == ReportName.CONVERSATIONS:
            return self._conversation_rows(session, filters)
        if report_name == ReportName.FAQ_UTILITY:
            return self._faq_utility_rows(session, filters)
        if report_name == ReportName.OPERATIONAL_ACTIVITY:
            return self._operational_activity_rows(session, filters)
        raise ValueError("Reporte no soportado")

    def _summary(
        self, session: Session, filters: ReportExportFilters
    ) -> dict[str, object]:
        conditions = self._ticket_conditions(filters, Ticket.created_at)
        statement = select(Ticket.status, func.count(Ticket.id)).select_from(Ticket)
        if filters.sla_compliant is not None:
            statement = statement.join(TicketSla, TicketSla.ticket_id == Ticket.id)
        grouped = session.exec(
            statement.where(*conditions).group_by(Ticket.status)
        ).all()
        counts = {self._value(status): int(count) for status, count in grouped}
        total = sum(counts.values())
        average = self._average_resolution_hours(session, filters)
        return {
            "total_tickets": total,
            "new_tickets": counts.get(TicketStatus.NUEVO.value, 0),
            "assigned_tickets": counts.get(TicketStatus.ASIGNADO.value, 0),
            "in_process_tickets": counts.get(TicketStatus.EN_PROCESO.value, 0),
            "pending_client_tickets": counts.get(
                TicketStatus.PENDIENTE_CLIENTE.value, 0
            ),
            "resolved_tickets": counts.get(TicketStatus.RESUELTO.value, 0),
            "closed_tickets": counts.get(TicketStatus.CERRADO.value, 0),
            "cancelled_tickets": counts.get(TicketStatus.CANCELADO.value, 0),
            "average_resolution_time_hours": average,
        }

    def _by_status(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        statement = select(Ticket.status, func.count(Ticket.id)).select_from(Ticket)
        rows = self._grouped_tickets(session, statement, filters, Ticket.status)
        return [
            {"status": self._value(status), "count": int(count)}
            for status, count in rows
        ]

    def _by_priority(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        statement = select(Ticket.priority, func.count(Ticket.id)).select_from(Ticket)
        rows = self._grouped_tickets(session, statement, filters, Ticket.priority)
        return [
            {"priority": self._value(priority), "count": int(count)}
            for priority, count in rows
        ]

    def _by_source(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        statement = select(Ticket.source, func.count(Ticket.id)).select_from(Ticket)
        rows = self._grouped_tickets(session, statement, filters, Ticket.source)
        return [
            {"source": self._value(source), "count": int(count)}
            for source, count in rows
        ]

    def _by_category(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        statement = (
            select(Ticket.category_id, TicketCategory.name, func.count(Ticket.id))
            .select_from(Ticket)
            .join(TicketCategory, TicketCategory.id == Ticket.category_id)
        )
        rows = self._grouped_tickets(
            session, statement, filters, Ticket.category_id, TicketCategory.name
        )
        return [
            {"category_id": category_id, "category_name": name, "count": int(count)}
            for category_id, name, count in rows
        ]

    def _by_advisor(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        advisor = aliased(User)
        statement = (
            select(Ticket.assigned_advisor_id, advisor.full_name, func.count(Ticket.id))
            .select_from(Ticket)
            .outerjoin(advisor, advisor.id == Ticket.assigned_advisor_id)
        )
        rows = self._grouped_tickets(
            session,
            statement,
            filters,
            Ticket.assigned_advisor_id,
            advisor.full_name,
        )
        return [
            {
                "advisor_id": advisor_id or "UNASSIGNED",
                "advisor_name": name or "Sin asignar",
                "count": int(count),
            }
            for advisor_id, name, count in rows
        ]

    def _grouped_tickets(
        self,
        session: Session,
        statement,
        filters: ReportExportFilters,
        *group_columns,
    ):
        conditions = self._ticket_conditions(filters, Ticket.created_at)
        if filters.sla_compliant is not None:
            statement = statement.join(TicketSla, TicketSla.ticket_id == Ticket.id)
        return session.exec(
            statement.where(*conditions)
            .group_by(*group_columns)
            .order_by(*group_columns)
        ).all()

    def _detail_rows(
        self,
        session: Session,
        filters: ReportExportFilters,
        date_column,
        *,
        require_resolved: bool = False,
        require_first_response: bool = False,
    ) -> list[dict[str, object]]:
        conditions = self._ticket_conditions(filters, date_column)
        if require_resolved:
            conditions.append(Ticket.resolved_at.is_not(None))
        if require_first_response:
            conditions.append(TicketSla.first_responded_at.is_not(None))
        advisor = aliased(User)
        statement = (
            select(
                Ticket.tracking_code,
                Ticket.subject,
                Ticket.status,
                Ticket.priority,
                Ticket.source,
                Ticket.created_at,
                Ticket.resolved_at,
                TicketCategory.name,
                Ticket.assigned_advisor_id,
                advisor.full_name,
                TicketSla.first_responded_at,
                TicketSla.first_response_within_sla,
                TicketSla.resolution_due_at,
                TicketSla.completed_within_sla,
                TicketSla.breached_at,
            )
            .select_from(Ticket)
            .join(TicketCategory, TicketCategory.id == Ticket.category_id)
            .outerjoin(advisor, advisor.id == Ticket.assigned_advisor_id)
            .outerjoin(TicketSla, TicketSla.ticket_id == Ticket.id)
            .where(*conditions)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        )
        total = self._count_tickets(session, conditions)
        self._check_limit(total, filters.limit)
        rows = session.exec(statement.limit(filters.limit)).all()
        result = []
        for row in rows:
            values = list(row)
            result.append(
                {
                    "tracking_code": values[0],
                    "subject": values[1],
                    "status": self._value(values[2]),
                    "priority": self._value(values[3]),
                    "source": self._value(values[4]),
                    "created_at_utc": values[5],
                    "resolved_at_utc": values[6],
                    "category": values[7],
                    "advisor_id": values[8] or "UNASSIGNED",
                    "advisor_name": values[9] or "Sin asignar",
                    "first_responded_at_utc": values[10],
                    "first_response_within_sla": values[11],
                    "resolution_due_at_utc": values[12],
                    "completed_within_sla": values[13],
                    "sla_breached_at_utc": values[14],
                    "resolution_time_hours": self._duration_hours(values[5], values[6]),
                    "first_response_time_hours": self._duration_hours(
                        values[5], values[10]
                    ),
                }
            )
        return result

    def _sla_rows(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        conditions = self._ticket_conditions(filters, Ticket.created_at)
        conditions.append(TicketSla.id.is_not(None))
        advisor = aliased(User)
        statement = (
            select(
                Ticket.tracking_code,
                Ticket.status,
                Ticket.priority,
                TicketCategory.name,
                advisor.full_name,
                TicketSla.first_responded_at,
                TicketSla.first_response_within_sla,
                TicketSla.resolved_at,
                TicketSla.completed_within_sla,
                TicketSla.status,
                TicketSla.breached_at,
            )
            .select_from(TicketSla)
            .join(Ticket, Ticket.id == TicketSla.ticket_id)
            .join(TicketCategory, TicketCategory.id == Ticket.category_id)
            .outerjoin(advisor, advisor.id == Ticket.assigned_advisor_id)
            .where(*conditions)
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        )
        total = self._count_tickets(session, conditions)
        self._check_limit(total, filters.limit)
        rows = session.exec(statement.limit(filters.limit)).all()
        return [
            {
                "tracking_code": row[0],
                "ticket_status": self._value(row[1]),
                "priority": self._value(row[2]),
                "category": row[3],
                "advisor_name": row[4] or "Sin asignar",
                "first_responded_at_utc": row[5],
                "first_response_within_sla": row[6],
                "resolved_at_utc": row[7],
                "completed_within_sla": row[8],
                "sla_status": self._value(row[9]),
                "breached_at_utc": row[10],
            }
            for row in rows
        ]

    def _conversation_rows(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        conditions = []
        if filters.from_date is not None:
            conditions.append(Conversation.started_at >= filters.from_date)
        if filters.to_date is not None:
            conditions.append(Conversation.started_at <= filters.to_date)
        conditions.extend(self._ticket_conditions(filters, None))
        statement = (
            select(
                func.count(Conversation.id),
                func.count(func.distinct(Ticket.id)),
                func.count(Conversation.id) - func.count(func.distinct(Ticket.id)),
            )
            .select_from(Conversation)
            .outerjoin(Ticket, Ticket.conversation_id == Conversation.id)
        )
        if filters.sla_compliant is not None:
            statement = statement.outerjoin(TicketSla, TicketSla.ticket_id == Ticket.id)
        total, converted, not_converted = session.exec(
            statement.where(*conditions)
        ).one()
        total = int(total or 0)
        converted = int(converted or 0)
        return [
            {
                "conversations": total,
                "converted_to_ticket": converted,
                "not_converted": int(not_converted or 0),
                "conversion_rate_percent": round(converted * 100 / total, 2)
                if total
                else 0.0,
            }
        ]

    def _faq_utility_rows(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        helpful = func.sum(case((FAQFeedback.is_helpful.is_(True), 1), else_=0))
        not_helpful = func.sum(case((FAQFeedback.is_helpful.is_(False), 1), else_=0))
        total = func.count(FAQFeedback.id)
        conditions = []
        if filters.from_date is not None:
            conditions.append(FAQFeedback.created_at >= filters.from_date)
        if filters.to_date is not None:
            conditions.append(FAQFeedback.created_at <= filters.to_date)
        if filters.category_id is not None:
            conditions.append(FAQ.category_id == filters.category_id)
        if filters.search:
            pattern = f"%{filters.search}%"
            conditions.append(
                or_(FAQ.title.ilike(pattern), FAQ.question.ilike(pattern))
            )
        statement = (
            select(FAQ.id, FAQ.title, total, helpful, not_helpful)
            .select_from(FAQFeedback)
            .join(FAQ, FAQ.id == FAQFeedback.faq_id)
            .where(*conditions)
            .group_by(FAQ.id, FAQ.title)
            .order_by(FAQ.title, FAQ.id)
        )
        rows = session.exec(statement.limit(filters.limit)).all()
        return [
            {
                "faq_id": row[0],
                "title": row[1],
                "total_feedback": int(row[2] or 0),
                "helpful": int(row[3] or 0),
                "not_helpful": int(row[4] or 0),
                "helpful_rate_percent": round((row[3] or 0) * 100 / row[2], 2)
                if row[2]
                else 0.0,
            }
            for row in rows
        ]

    def _operational_activity_rows(
        self, session: Session, filters: ReportExportFilters
    ) -> list[dict[str, object]]:
        conditions = self._ticket_conditions(filters, TicketHistory.created_at)
        statement = (
            select(TicketHistory.action, func.count(TicketHistory.id))
            .select_from(TicketHistory)
            .join(Ticket, Ticket.id == TicketHistory.ticket_id)
            .group_by(TicketHistory.action)
            .order_by(TicketHistory.action)
        )
        if filters.sla_compliant is not None:
            statement = statement.outerjoin(TicketSla, TicketSla.ticket_id == Ticket.id)
        rows = session.exec(statement.where(*conditions).limit(filters.limit)).all()
        return [{"action": action, "count": int(count)} for action, count in rows]

    def _average_resolution_hours(
        self, session: Session, filters: ReportExportFilters
    ) -> float:
        conditions = self._ticket_conditions(filters, Ticket.created_at)
        if session.get_bind().dialect.name == "sqlite":
            expression = func.avg(
                (func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))
                * 24
            )
        else:
            expression = (
                func.avg(
                    func.timestampdiff(
                        literal_column("SECOND"),
                        Ticket.created_at,
                        Ticket.resolved_at,
                    )
                )
                / 3600
            )
        statement = select(expression).select_from(Ticket)
        if filters.sla_compliant is not None:
            statement = statement.join(TicketSla, TicketSla.ticket_id == Ticket.id)
        value = session.exec(
            statement.where(*conditions, Ticket.resolved_at.is_not(None))
        ).one()
        return round(float(value or 0), 2)

    def _count_tickets(
        self,
        session: Session,
        conditions,
    ) -> int:
        statement = select(func.count(Ticket.id)).select_from(Ticket)
        statement = statement.outerjoin(TicketSla, TicketSla.ticket_id == Ticket.id)
        return int(session.exec(statement.where(*conditions)).one() or 0)

    @staticmethod
    def _check_limit(total: int, limit: int) -> None:
        if total > limit:
            raise ExportLimitExceeded(total, limit)

    @staticmethod
    def _ticket_conditions(filters: ReportExportFilters, date_column) -> list[object]:
        conditions: list[object] = []
        if date_column is not None:
            if filters.from_date is not None:
                conditions.append(date_column >= filters.from_date)
            if filters.to_date is not None:
                conditions.append(date_column <= filters.to_date)
        if filters.status is not None:
            conditions.append(Ticket.status == filters.status)
        if filters.priority is not None:
            conditions.append(Ticket.priority == filters.priority)
        if filters.category_id is not None:
            conditions.append(Ticket.category_id == filters.category_id)
        if filters.source is not None:
            conditions.append(Ticket.source == filters.source)
        if filters.advisor_id is not None:
            conditions.append(Ticket.assigned_advisor_id == filters.advisor_id)
        if filters.client_id is not None:
            conditions.append(Ticket.client_id == filters.client_id)
        if filters.sla_compliant is not None:
            conditions.append(TicketSla.completed_within_sla == filters.sla_compliant)
        if filters.search:
            pattern = f"%{filters.search}%"
            conditions.append(
                or_(
                    Ticket.tracking_code.ilike(pattern),
                    Ticket.subject.ilike(pattern),
                    Ticket.description.ilike(pattern),
                )
            )
        return conditions

    @staticmethod
    def _duration_hours(start: datetime | None, end: datetime | None) -> float | None:
        if start is None or end is None:
            return None
        return round((end - start).total_seconds() / 3600, 2)

    @staticmethod
    def _value(value: object) -> object:
        return getattr(value, "value", value)
