import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.reportes.models.export_audit import ReportExportAudit
from app.modules.reportes.repositories.report_repository import (
    ExportLimitExceeded,
    ReportRepository,
)
from app.modules.reportes.schemas.report import (
    CategoryReport,
    PriorityReport,
    ReportExportFilters,
    ReportFormat,
    ReportItemCategory,
    ReportItemPriority,
    ReportItemStatus,
    ReportName,
    ResolutionTimeReport,
    StatusReport,
    SummaryReport,
)
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus


@dataclass(frozen=True)
class ExportResult:
    content: bytes
    filename: str
    row_count: int


class ReportService:
    def __init__(
        self,
        repository: ReportRepository | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or ReportRepository()
        self.audit = audit_service or AuditService()

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

    def export(
        self,
        session: Session,
        actor: AuthenticatedUser,
        report_name: ReportName,
        export_format: ReportFormat,
        filters: ReportExportFilters,
    ) -> ExportResult:
        if actor.role != "SUPERVISOR":
            self._write_audit(
                session,
                actor,
                report_name,
                export_format,
                filters,
                0,
                False,
                "forbidden",
            )
            session.commit()
            raise HTTPException(
                status_code=403, detail="Solo un supervisor puede exportar reportes"
            )
        try:
            filters = self._normalize_filters(filters)
            if export_format == ReportFormat.XLSX:
                raise HTTPException(
                    status_code=422,
                    detail="El formato XLSX no está disponible en este entorno",
                )
            rows = self.repository.export_rows(session, report_name, filters)
            generated_at = datetime.now(UTC)
            content = self._to_csv(report_name, rows, filters, generated_at)
            self._write_audit(
                session,
                actor,
                report_name,
                export_format,
                filters,
                len(rows),
                True,
                None,
            )
            session.commit()
            filename = (
                f"report_{report_name.value}_"
                f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}.csv"
            )
            return ExportResult(content=content, filename=filename, row_count=len(rows))
        except ExportLimitExceeded as error:
            self._write_audit(
                session,
                actor,
                report_name,
                export_format,
                filters,
                error.total,
                False,
                "row_limit",
            )
            session.commit()
            raise HTTPException(
                status_code=422,
                detail=(
                    f"La exportación supera el límite de {error.limit} filas "
                    f"({error.total} coincidencias)"
                ),
            ) from error
        except HTTPException as error:
            self._write_audit(
                session,
                actor,
                report_name,
                export_format,
                filters,
                0,
                False,
                self._error_code(error),
            )
            session.commit()
            raise
        except Exception as error:
            session.rollback()
            self._write_audit(
                session,
                actor,
                report_name,
                export_format,
                filters,
                0,
                False,
                "internal_error",
            )
            session.commit()
            raise HTTPException(
                status_code=500, detail="No se pudo generar el reporte"
            ) from error

    @staticmethod
    def _normalize_filters(filters: ReportExportFilters) -> ReportExportFilters:
        for value in (filters.from_date, filters.to_date):
            if value is not None and value.tzinfo is None:
                raise HTTPException(
                    status_code=422,
                    detail="Las fechas deben incluir una zona horaria explícita",
                )
        if (
            filters.from_date is not None
            and filters.to_date is not None
            and filters.from_date > filters.to_date
        ):
            raise HTTPException(
                status_code=422, detail="El rango de fechas no es válido"
            )
        settings = get_settings()
        if (
            filters.from_date is not None
            and filters.to_date is not None
            and (filters.to_date - filters.from_date).total_seconds()
            > settings.report_export_max_range_days * 86_400
        ):
            raise HTTPException(
                status_code=422,
                detail="El rango de fechas supera el máximo permitido",
            )
        if filters.limit > settings.report_export_max_rows:
            raise HTTPException(
                status_code=422,
                detail="El límite solicitado supera el máximo de exportación",
            )
        return filters.model_copy(
            update={
                "from_date": filters.from_date.astimezone(UTC)
                if filters.from_date is not None
                else None,
                "to_date": filters.to_date.astimezone(UTC)
                if filters.to_date is not None
                else None,
            }
        )

    @classmethod
    def _to_csv(
        cls,
        report_name: ReportName,
        rows: list[dict[str, object]],
        filters: ReportExportFilters,
        generated_at: datetime,
    ) -> bytes:
        headers = cls._headers(report_name, rows)
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(["generated_at_utc", generated_at.isoformat()])
        writer.writerow(
            [
                "applied_filters",
                json.dumps(
                    filters.model_dump(mode="json", exclude_none=True),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        )
        writer.writerow(headers)
        for row in rows:
            writer.writerow([cls._safe_cell(row.get(header)) for header in headers])
        return output.getvalue().encode("utf-8")

    @staticmethod
    def _headers(report_name: ReportName, rows: list[dict[str, object]]) -> list[str]:
        headers = {
            ReportName.SUMMARY: [
                "total_tickets",
                "new_tickets",
                "assigned_tickets",
                "in_process_tickets",
                "pending_client_tickets",
                "resolved_tickets",
                "closed_tickets",
                "cancelled_tickets",
                "average_resolution_time_hours",
            ],
            ReportName.BY_STATUS: ["status", "count"],
            ReportName.BY_PRIORITY: ["priority", "count"],
            ReportName.BY_CATEGORY: ["category_id", "category_name", "count"],
            ReportName.BY_SOURCE: ["source", "count"],
            ReportName.BY_ADVISOR: ["advisor_id", "advisor_name", "count"],
            ReportName.CREATED_TICKETS: [
                "tracking_code",
                "subject",
                "status",
                "priority",
                "source",
                "created_at_utc",
                "category",
                "advisor_id",
                "advisor_name",
            ],
            ReportName.RESOLVED_TICKETS: [
                "tracking_code",
                "subject",
                "status",
                "priority",
                "source",
                "created_at_utc",
                "resolved_at_utc",
                "category",
                "advisor_name",
                "resolution_time_hours",
            ],
            ReportName.FIRST_RESPONSE_TIME: [
                "tracking_code",
                "subject",
                "created_at_utc",
                "first_responded_at_utc",
                "first_response_time_hours",
                "first_response_within_sla",
            ],
            ReportName.RESOLUTION_TIME: [
                "tracking_code",
                "subject",
                "created_at_utc",
                "resolved_at_utc",
                "resolution_time_hours",
            ],
            ReportName.SLA_COMPLIANCE: [
                "tracking_code",
                "ticket_status",
                "priority",
                "category",
                "advisor_name",
                "first_response_within_sla",
                "completed_within_sla",
                "sla_status",
                "breached_at_utc",
            ],
            ReportName.CONVERSATIONS: [
                "conversations",
                "converted_to_ticket",
                "not_converted",
                "conversion_rate_percent",
            ],
            ReportName.FAQ_UTILITY: [
                "faq_id",
                "title",
                "total_feedback",
                "helpful",
                "not_helpful",
                "helpful_rate_percent",
            ],
            ReportName.OPERATIONAL_ACTIVITY: ["action", "count"],
        }
        return headers[report_name]

    @staticmethod
    def _safe_cell(value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return (
                value.astimezone(UTC).isoformat()
                if value.tzinfo
                else value.replace(tzinfo=UTC).isoformat()
            )
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            value = getattr(value, "value", value)
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return f"'{value}"
        return value

    def _write_audit(
        self,
        session: Session,
        actor: AuthenticatedUser,
        report_name: ReportName,
        export_format: ReportFormat,
        filters: ReportExportFilters,
        row_count: int,
        succeeded: bool,
        error_code: str | None,
    ) -> None:
        values = filters.model_dump(mode="json", exclude_none=True)
        values.pop("search", None)
        session.add(
            ReportExportAudit(
                actor_id=actor.user.id,
                report_name=report_name.value,
                export_format=export_format.value,
                filters_json=values,
                row_count=row_count,
                succeeded=succeeded,
                error_code=error_code,
            )
        )
        self.audit.record(
            session,
            event_type="REPORT",
            action="EXPORT_SUCCEEDED" if succeeded else "EXPORT_FAILED",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="REPORT",
            resource_id=report_name.value,
            success=succeeded,
            error_code=error_code,
            metadata={
                "format": export_format.value,
                "row_count": row_count,
                "filters": values,
            },
        )

    @staticmethod
    def _error_code(error: HTTPException) -> str:
        if error.status_code == 422:
            return "invalid_request"
        if error.status_code == 403:
            return "forbidden"
        return "export_failed"

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
