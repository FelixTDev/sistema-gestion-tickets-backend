from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.sla import (
    SlaPolicy,
    SlaPolicyHistory,
    SlaStatus,
    TicketSla,
)
from app.modules.tickets.models.ticket import Ticket, TicketPriority, TicketStatus
from app.modules.tickets.repositories.sla_repository import SlaRepository
from app.modules.tickets.schemas.sla import (
    SlaPolicyCreate,
    SlaPolicyUpdate,
    TicketSlaRead,
)
from app.shared.datetime import as_utc


@dataclass(frozen=True)
class SlaEvaluationResult:
    evaluated: int
    warnings: int
    breaches: int


class SlaService:
    def __init__(
        self,
        repository: SlaRepository | None = None,
        notification_service: NotificationService | None = None,
        clock: Callable[[], datetime] | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or SlaRepository()
        self.notifications = notification_service or NotificationService()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.audit = audit_service or AuditService()

    @staticmethod
    def policy_key(
        priority: TicketPriority | None,
        category_id: str | None,
        source: str | None,
    ) -> str:
        priority_value = priority.value if priority is not None else "*"
        source_value = source.value if hasattr(source, "value") else source or "*"
        return (
            f"priority={priority_value}|category={category_id or '*'}|"
            f"source={source_value}"
        )

    def start_for_ticket(
        self, session: Session, ticket: Ticket, actor_id: str | None = None
    ) -> TicketSla:
        existing = self.repository.get_ticket_sla(session, ticket.id)
        if existing is not None:
            return existing
        started_at = self._utc(ticket.created_at)
        if started_at > self._now():
            raise HTTPException(
                status_code=422,
                detail="La fecha de creación no puede estar en el futuro",
            )
        policy = self.repository.find_policy_for_ticket(
            session, ticket.priority, ticket.category_id, ticket.source
        )
        if policy is None:
            raise HTTPException(
                status_code=422,
                detail="No existe una política SLA activa para el ticket",
            )
        sla = TicketSla(
            ticket_id=ticket.id,
            policy_id=policy.id,
            started_at=started_at,
            first_response_due_at=started_at
            + timedelta(seconds=policy.first_response_seconds),
            resolution_due_at=started_at + timedelta(seconds=policy.resolution_seconds),
        )
        self.repository.add_ticket_sla(session, sla)
        self._ticket_history(
            session,
            ticket.id,
            actor_id,
            "SLA_STARTED",
            None,
            policy.id,
            "SLA iniciado con la creación del ticket",
        )
        return sla

    def get_for_ticket(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> tuple[TicketSla, SlaPolicy]:
        ticket = self._authorized_ticket(session, ticket_id, actor)
        sla = self.repository.get_ticket_sla(session, ticket.id)
        if sla is None:
            sla = self.start_for_ticket(session, ticket, actor.user.id)
            session.commit()
        policy = self.repository.get_policy(session, sla.policy_id)
        if policy is None:
            raise HTTPException(status_code=422, detail="La política SLA no existe")
        return sla, policy

    def record_first_response(
        self,
        session: Session,
        ticket_id: str,
        actor_id: str,
        actor_role: str,
        responded_at: datetime,
    ) -> TicketSla | None:
        if actor_role not in {"ASESOR", "SUPERVISOR"}:
            return self.repository.get_ticket_sla(session, ticket_id)
        sla = self.repository.get_ticket_sla(session, ticket_id)
        if sla is None:
            raise HTTPException(status_code=422, detail="El ticket no tiene SLA activo")
        response_at = self._utc(responded_at)
        if response_at > self._now():
            raise HTTPException(
                status_code=422,
                detail="La primera respuesta no puede estar en el futuro",
            )
        if sla.first_responded_at is not None:
            return sla
        sla.first_responded_at = response_at
        sla.first_response_within_sla = response_at <= self._utc(
            sla.first_response_due_at
        )
        if not sla.first_response_within_sla:
            sla.first_response_breached_at = response_at
            self._mark_breached(sla, response_at)
            ticket = session.get(Ticket, ticket_id)
            if ticket is not None:
                self._send_breach(
                    session,
                    sla,
                    ticket,
                    ticket.assigned_advisor_id,
                    response_at,
                    "first_response",
                )
        session.add(sla)
        self._ticket_history(
            session,
            ticket_id,
            actor_id,
            "SLA_FIRST_RESPONSE",
            None,
            response_at.isoformat(),
            "Primera respuesta operativa registrada",
        )
        return sla

    def on_status_change(
        self,
        session: Session,
        ticket: Ticket,
        old_status: TicketStatus,
        new_status: TicketStatus,
        actor_id: str,
    ) -> TicketSla:
        sla = self.repository.get_ticket_sla(session, ticket.id)
        if sla is None:
            sla = self.start_for_ticket(session, ticket, actor_id)
        now = self._now()
        if new_status == TicketStatus.RESUELTO:
            self._finalize_missing_first_response(session, sla, ticket, now)
            sla.resolved_at = now
            sla.completed_within_sla = now <= self._utc(sla.resolution_due_at)
            if not sla.completed_within_sla and sla.resolution_breached_at is None:
                sla.resolution_breached_at = now
                self._mark_breached(sla, now)
                self._send_breach(
                    session,
                    sla,
                    ticket,
                    ticket.assigned_advisor_id,
                    now,
                    "resolution",
                )
            sla.status = SlaStatus.COMPLETED
        elif new_status == TicketStatus.CERRADO:
            self._finalize_missing_first_response(session, sla, ticket, now)
            if sla.resolved_at is None:
                sla.resolved_at = now
                sla.completed_within_sla = now <= self._utc(sla.resolution_due_at)
            sla.status = SlaStatus.COMPLETED
        elif new_status == TicketStatus.CANCELADO:
            sla.paused_at = None
            sla.status = SlaStatus.CANCELLED
        elif (
            old_status == TicketStatus.RESUELTO
            and new_status == TicketStatus.EN_PROCESO
        ):
            sla.reopen_count += 1
            sla.resolved_at = None
            sla.completed_within_sla = None
            sla.resolution_breached_at = None
            sla.status = SlaStatus.ACTIVE
            self._ticket_history(
                session,
                ticket.id,
                actor_id,
                "SLA_REOPENED",
                SlaStatus.COMPLETED,
                SlaStatus.ACTIVE,
                "El SLA continúa después de la reapertura",
            )
        sla.updated_at = now
        session.add(sla)
        if new_status != TicketStatus.EN_PROCESO or old_status != TicketStatus.RESUELTO:
            self._ticket_history(
                session,
                ticket.id,
                actor_id,
                "SLA_STATUS_CHANGED",
                old_status,
                new_status,
                "Estado SLA actualizado por cambio de estado del ticket",
            )
        return sla

    def pause(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        reason: str,
    ) -> tuple[TicketSla, SlaPolicy]:
        ticket = self._authorized_staff_ticket(session, ticket_id, actor)
        sla, policy = self.get_for_ticket(session, ticket.id, actor)
        if ticket.status in {TicketStatus.CERRADO, TicketStatus.CANCELADO}:
            raise HTTPException(status_code=409, detail="El ticket no puede pausarse")
        if sla.status != SlaStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="El SLA no está activo")
        now = self._now()
        sla.paused_at = now
        sla.status = SlaStatus.PAUSED
        sla.updated_at = now
        session.add(sla)
        self._ticket_history(
            session,
            ticket.id,
            actor.user.id,
            "SLA_PAUSED",
            SlaStatus.ACTIVE,
            SlaStatus.PAUSED,
            reason,
        )
        session.commit()
        session.refresh(sla)
        return sla, policy

    def resume(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> tuple[TicketSla, SlaPolicy]:
        ticket = self._authorized_staff_ticket(session, ticket_id, actor)
        sla, policy = self.get_for_ticket(session, ticket.id, actor)
        if sla.status != SlaStatus.PAUSED or sla.paused_at is None:
            raise HTTPException(status_code=409, detail="El SLA no está pausado")
        now = self._now()
        paused_at = self._utc(sla.paused_at)
        if now < paused_at:
            raise HTTPException(
                status_code=422,
                detail="La reanudación no puede ser anterior a la pausa",
            )
        paused_seconds = int((now - paused_at).total_seconds())
        extension = timedelta(seconds=paused_seconds)
        sla.first_response_due_at = self._utc(sla.first_response_due_at) + extension
        sla.resolution_due_at = self._utc(sla.resolution_due_at) + extension
        sla.total_paused_seconds += paused_seconds
        sla.paused_at = None
        sla.status = SlaStatus.ACTIVE
        sla.updated_at = now
        session.add(sla)
        self._ticket_history(
            session,
            ticket.id,
            actor.user.id,
            "SLA_RESUMED",
            SlaStatus.PAUSED,
            SlaStatus.ACTIVE,
            f"SLA reanudado después de {paused_seconds} segundos",
        )
        session.commit()
        session.refresh(sla)
        return sla, policy

    def evaluate(self, session: Session) -> SlaEvaluationResult:
        now = self._now()
        evaluated = warnings = breaches = 0
        for sla, ticket, advisor_id, policy in self.repository.list_evaluable(session):
            evaluated += 1
            if sla.status == SlaStatus.PAUSED:
                continue
            if (
                sla.first_responded_at is None
                and now < self._utc(sla.first_response_due_at)
                and self._warning_due(
                    sla.first_response_due_at, now, policy.warning_seconds
                )
                and sla.first_response_warning_sent_at is None
            ):
                self._send_warning(
                    session, sla, ticket, advisor_id, now, "first_response"
                )
                warnings += 1
            if (
                sla.resolved_at is None
                and now < self._utc(sla.resolution_due_at)
                and self._warning_due(
                    sla.resolution_due_at, now, policy.warning_seconds
                )
                and sla.resolution_warning_sent_at is None
            ):
                self._send_warning(session, sla, ticket, advisor_id, now, "resolution")
                warnings += 1
            if (
                sla.first_responded_at is None
                and now >= self._utc(sla.first_response_due_at)
                and sla.first_response_breached_at is None
            ):
                sla.first_response_breached_at = now
                self._mark_breached(sla, now)
                self._send_breach(
                    session, sla, ticket, advisor_id, now, "first_response"
                )
                breaches += 1
            if (
                sla.resolved_at is None
                and now >= self._utc(sla.resolution_due_at)
                and sla.resolution_breached_at is None
            ):
                sla.resolution_breached_at = now
                self._mark_breached(sla, now)
                self._send_breach(session, sla, ticket, advisor_id, now, "resolution")
                breaches += 1
            sla.updated_at = now
            session.add(sla)
        session.commit()
        return SlaEvaluationResult(evaluated, warnings, breaches)

    def evaluate_as_supervisor(
        self, session: Session, actor: AuthenticatedUser
    ) -> SlaEvaluationResult:
        self._require_supervisor(actor)
        return self.evaluate(session)

    def list_policies(
        self, session: Session, actor: AuthenticatedUser
    ) -> list[SlaPolicy]:
        self._require_supervisor(actor)
        return self.repository.list_policies(session)

    def create_policy(
        self, session: Session, data: SlaPolicyCreate, actor: AuthenticatedUser
    ) -> SlaPolicy:
        self._require_supervisor(actor)
        self._validate_category(session, data.category_id)
        key = self.policy_key(data.priority, data.category_id, data.source)
        if self.repository.get_policy_by_key(session, key) is not None:
            raise HTTPException(status_code=409, detail="La política SLA ya existe")
        now = self._now()
        policy = SlaPolicy(
            policy_key=key,
            priority=data.priority,
            category_id=data.category_id,
            source=data.source,
            first_response_seconds=data.first_response_seconds,
            resolution_seconds=data.resolution_seconds,
            warning_seconds=data.warning_seconds,
            timezone_name=data.timezone_name,
            calendar_name=data.calendar_name,
            is_active=data.is_active,
            created_at=now,
            updated_at=now,
        )
        self.repository.add_policy(session, policy)
        self._policy_history(
            session, policy, actor.user.id, "CREATED", {}, self._policy_values(policy)
        )
        self.audit.record(
            session,
            event_type="SLA",
            action="POLICY_CREATED",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="SLA_POLICY",
            resource_id=policy.id,
            success=True,
            after_data=self._policy_values(policy),
        )
        session.commit()
        session.refresh(policy)
        return policy

    def update_policy(
        self,
        session: Session,
        policy_id: str,
        data: SlaPolicyUpdate,
        actor: AuthenticatedUser,
    ) -> SlaPolicy:
        self._require_supervisor(actor)
        policy = self.repository.get_policy(session, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="Política SLA no encontrada")
        old_values = self._policy_values(policy)
        values = data.model_dump(exclude_unset=True)
        nullable_fields = {
            "first_response_seconds",
            "resolution_seconds",
            "warning_seconds",
            "timezone_name",
            "calendar_name",
        }
        if any(field in values and values[field] is None for field in nullable_fields):
            raise HTTPException(
                status_code=422,
                detail="Los valores operativos de la política no pueden ser nulos",
            )
        category_id = values.get("category_id", policy.category_id)
        self._validate_category(session, category_id)
        for field, value in values.items():
            setattr(policy, field, value)
        policy.policy_key = self.policy_key(
            policy.priority, policy.category_id, policy.source
        )
        existing = self.repository.get_policy_by_key(session, policy.policy_key)
        if existing is not None and existing.id != policy.id:
            raise HTTPException(status_code=409, detail="La política SLA ya existe")
        policy.updated_at = self._now()
        session.add(policy)
        self._policy_history(
            session,
            policy,
            actor.user.id,
            "UPDATED",
            old_values,
            self._policy_values(policy),
        )
        self.audit.record(
            session,
            event_type="SLA",
            action="POLICY_UPDATED",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="SLA_POLICY",
            resource_id=policy.id,
            success=True,
            before_data=old_values,
            after_data=self._policy_values(policy),
        )
        session.commit()
        session.refresh(policy)
        return policy

    def to_read(self, sla: TicketSla, policy: SlaPolicy) -> TicketSlaRead:
        values: dict[str, Any] = {
            **sla.model_dump(),
            "policy_priority": policy.priority,
            "policy_category_id": policy.category_id,
            "policy_source": policy.source,
            "policy_timezone_name": policy.timezone_name,
            "policy_calendar_name": policy.calendar_name,
        }
        return TicketSlaRead.model_validate(values)

    def _send_warning(
        self,
        session: Session,
        sla: TicketSla,
        ticket: Ticket,
        advisor_id: str | None,
        now: datetime,
        stage: str,
    ) -> None:
        if stage == "first_response":
            sla.first_response_warning_sent_at = now
        else:
            sla.resolution_warning_sent_at = now
        if sla.warning_sent_at is None:
            sla.warning_sent_at = now
            sla.warning_stage = stage
        recipient_id = advisor_id or ticket.client_id
        self.notifications.create(
            session,
            recipient_user_id=recipient_id,
            notification_type=NotificationType.SLA_WARNING,
            title="SLA próximo a vencer",
            message="Un tiempo operativo de tu ticket está próximo a vencer.",
            related_ticket_id=ticket.id,
            metadata={"stage": stage},
            dedupe_key=f"sla_warning:{sla.id}:{stage}:{sla.reopen_count}",
        )
        self._ticket_history(
            session,
            ticket.id,
            None,
            "SLA_WARNING",
            None,
            stage,
            "Advertencia SLA emitida",
        )

    def _send_breach(
        self,
        session: Session,
        sla: TicketSla,
        ticket: Ticket,
        advisor_id: str | None,
        now: datetime,
        stage: str,
    ) -> None:
        recipient_id = advisor_id or ticket.client_id
        self.notifications.create(
            session,
            recipient_user_id=recipient_id,
            notification_type=NotificationType.SLA_BREACHED,
            title="SLA vencido",
            message="Un tiempo operativo de tu ticket venció sin completarse.",
            related_ticket_id=ticket.id,
            metadata={"stage": stage},
            dedupe_key=f"sla_breached:{sla.id}:{stage}:{sla.reopen_count}",
        )
        self._ticket_history(
            session,
            ticket.id,
            None,
            "SLA_BREACHED",
            None,
            stage,
            "Incumplimiento SLA registrado",
        )

    def _finalize_missing_first_response(
        self,
        session: Session,
        sla: TicketSla,
        ticket: Ticket,
        now: datetime,
    ) -> None:
        if sla.first_responded_at is not None:
            return
        sla.first_response_within_sla = False
        if sla.first_response_breached_at is None:
            sla.first_response_breached_at = now
            self._mark_breached(sla, now)
            self._send_breach(
                session,
                sla,
                ticket,
                ticket.assigned_advisor_id,
                now,
                "first_response",
            )

    def _warning_due(
        self, due_at: datetime, now: datetime, warning_seconds: int
    ) -> bool:
        return now >= self._utc(due_at) - timedelta(seconds=warning_seconds)

    @staticmethod
    def _mark_breached(sla: TicketSla, now: datetime) -> None:
        if sla.breached_at is None:
            sla.breached_at = now
        sla.status = SlaStatus.BREACHED

    def _authorized_ticket(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        ticket = session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        if actor.role == "CLIENTE" and ticket.client_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role not in {"CLIENTE", "ASESOR", "SUPERVISOR"}:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        return ticket

    def _authorized_staff_ticket(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        if actor.role not in {"ASESOR", "SUPERVISOR"}:
            raise HTTPException(status_code=403, detail="Se requiere un rol interno")
        return self._authorized_ticket(session, ticket_id, actor)

    @staticmethod
    def _require_supervisor(actor: AuthenticatedUser) -> None:
        if actor.role != "SUPERVISOR":
            raise HTTPException(status_code=403, detail="Se requiere rol SUPERVISOR")

    @staticmethod
    def _validate_category(session: Session, category_id: str | None) -> None:
        if category_id is not None and session.get(TicketCategory, category_id) is None:
            raise HTTPException(status_code=422, detail="La categoría no existe")

    def _policy_history(
        self,
        session: Session,
        policy: SlaPolicy,
        actor_id: str,
        action: str,
        old_values: dict[str, object],
        new_values: dict[str, object],
    ) -> None:
        self.repository.add_policy_history(
            session,
            SlaPolicyHistory(
                policy_id=policy.id,
                actor_id=actor_id,
                action=action,
                old_values=old_values,
                new_values=new_values,
            ),
        )

    @staticmethod
    def _policy_values(policy: SlaPolicy) -> dict[str, object]:
        return {
            "policy_key": policy.policy_key,
            "priority": policy.priority.value if policy.priority else None,
            "category_id": policy.category_id,
            "source": policy.source.value if policy.source else None,
            "first_response_seconds": policy.first_response_seconds,
            "resolution_seconds": policy.resolution_seconds,
            "warning_seconds": policy.warning_seconds,
            "timezone_name": policy.timezone_name,
            "calendar_name": policy.calendar_name,
            "is_active": policy.is_active,
        }

    def _ticket_history(
        self,
        session: Session,
        ticket_id: str,
        actor_id: str | None,
        action: str,
        old_value: object,
        new_value: object,
        description: str,
    ) -> None:
        session.add(
            TicketHistory(
                ticket_id=ticket_id,
                actor_id=actor_id,
                action=action,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                description=description,
            )
        )
        self.audit.record(
            session,
            event_type="SLA",
            action=action,
            actor_user_id=actor_id,
            actor_role=None,
            resource_type="TICKET",
            resource_id=ticket_id,
            success=True,
            before_data={"value": old_value},
            after_data={"value": new_value},
            metadata={"description": description},
        )

    def _now(self) -> datetime:
        now = self._utc(self.clock())
        return now

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return as_utc(value)
