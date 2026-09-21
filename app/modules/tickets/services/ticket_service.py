from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.chatbot.models.conversation import ConversationStatus
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.tickets.models.assignment import TicketAssignment
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import (
    Ticket,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.modules.tickets.repositories.ticket_repository import TicketRepository
from app.modules.tickets.schemas.operations import OperationalQueue
from app.modules.tickets.schemas.ticket import (
    AssignmentCreate,
    CommentCreate,
    ReasonRequest,
    StatusChange,
    TicketCreate,
)
from app.modules.tickets.services.sla_service import SlaService
from app.shared.pagination import PaginationResult

ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.NUEVO: {TicketStatus.ASIGNADO, TicketStatus.CANCELADO},
    TicketStatus.ASIGNADO: {TicketStatus.EN_PROCESO, TicketStatus.CANCELADO},
    TicketStatus.EN_PROCESO: {
        TicketStatus.PENDIENTE_CLIENTE,
        TicketStatus.RESUELTO,
        TicketStatus.CANCELADO,
    },
    TicketStatus.PENDIENTE_CLIENTE: {TicketStatus.EN_PROCESO},
    TicketStatus.RESUELTO: {
        TicketStatus.CERRADO,
        TicketStatus.EN_PROCESO,
        TicketStatus.CANCELADO,
    },
    TicketStatus.CERRADO: set(),
    TicketStatus.CANCELADO: set(),
}


class TicketService:
    def __init__(
        self,
        repository: TicketRepository | None = None,
        notification_service: NotificationService | None = None,
        sla_service: SlaService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or TicketRepository()
        self.notifications = notification_service or NotificationService()
        self.sla = sla_service or SlaService(notification_service=self.notifications)
        self.audit = audit_service or AuditService()

    def create_manual(
        self, session: Session, data: TicketCreate, actor: AuthenticatedUser
    ) -> Ticket:
        self._require_client(actor)
        return self._create(session, data, actor.user.id, actor, TicketSource.MANUAL)

    def convert_conversation(
        self,
        session: Session,
        conversation_id: str,
        data: TicketCreate,
        actor: AuthenticatedUser,
    ) -> Ticket:
        self._require_client(actor)
        conversation = self.repository.conversation(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        if conversation.user_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Conversación no autorizada")
        if conversation.status in {
            ConversationStatus.CONVERTED_TO_TICKET,
            ConversationStatus.CLOSED,
            ConversationStatus.EXPIRED,
        }:
            raise HTTPException(
                status_code=409, detail="La conversación no está disponible"
            )
        if self.repository.get_by_conversation(session, conversation_id) is not None:
            raise HTTPException(
                status_code=409, detail="La conversación ya tiene un ticket"
            )
        if (
            self.repository.count_chatbot_tickets_since(
                session,
                actor.user.id,
                datetime.now(UTC) - timedelta(days=1),
            )
            >= get_settings().chatbot_max_conversions_per_day
        ):
            raise HTTPException(
                status_code=429,
                detail="Se alcanzó el límite diario de conversiones",
            )
        bot_message = self.repository.last_bot_message(session, conversation_id)
        if bot_message is None or (
            bot_message.confidence is not None and float(bot_message.confidence) > 0.35
        ):
            raise HTTPException(
                status_code=409,
                detail="Solo una conversación no resuelta puede convertirse en ticket",
            )
        ticket = self._create(
            session, data, actor.user.id, actor, TicketSource.CHATBOT, conversation_id
        )
        now = datetime.now(UTC)
        conversation.status = ConversationStatus.CONVERTED_TO_TICKET
        conversation.converted_at = now
        conversation.ended_at = now
        conversation.last_activity_at = now
        session.add(conversation)
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="CONVERTED_TO_TICKET",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=actor.user.id,
            success=True,
            metadata={"ticket_id": ticket.id, "source": TicketSource.CHATBOT.value},
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def list_mine(
        self,
        session: Session,
        actor: AuthenticatedUser,
        *,
        page: int | None = None,
        page_size: int | None = None,
        status_filter: TicketStatus | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        source: TicketSource | None = None,
        assigned_advisor_id: str | None = None,
        search: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[Ticket] | PaginationResult[Ticket]:
        self._require_client(actor)
        return self._list(
            session,
            page=page,
            page_size=page_size,
            client_id=actor.user.id,
            status=status_filter,
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

    def list_global(
        self,
        session: Session,
        actor: AuthenticatedUser,
        status_filter: TicketStatus | None,
        category_id: str | None,
        priority: TicketPriority | None,
        created_from: datetime | None,
        created_to: datetime | None,
        *,
        page: int | None = None,
        page_size: int | None = None,
        source: TicketSource | None = None,
        client_id: str | None = None,
        assigned_advisor_id: str | None = None,
        search: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[Ticket] | PaginationResult[Ticket]:
        self._require_staff(actor)
        if actor.role == "ASESOR":
            assigned_advisor_id = actor.user.id
        return self._list(
            session,
            page=page,
            page_size=page_size,
            client_id=client_id,
            status=status_filter,
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

    def list_operational(
        self,
        session: Session,
        actor: AuthenticatedUser,
        queue: OperationalQueue,
        *,
        page: int,
        page_size: int,
        status_filter: TicketStatus | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        source: TicketSource | None = None,
        search: str | None = None,
        advisor_id: str | None = None,
        recent_hours: int = 24,
    ) -> PaginationResult[Ticket]:
        self._require_staff(actor)
        if not 1 <= recent_hours <= 168:
            raise HTTPException(
                status_code=422, detail="recent_hours debe estar entre 1 y 168"
            )
        assigned_advisor_id: str | None = None
        if queue == OperationalQueue.ASSIGNED_TO_ME:
            if actor.role != "ASESOR":
                raise HTTPException(
                    status_code=403,
                    detail="Solo un asesor puede consultar su bandeja personal",
                )
            assigned_advisor_id = actor.user.id
        elif queue == OperationalQueue.ASSIGNED_TO_ADVISOR:
            self._require_supervisor(actor)
            if advisor_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="advisor_id es obligatorio para esta bandeja",
                )
            if not self.repository.advisor_exists(session, advisor_id):
                raise HTTPException(
                    status_code=422, detail="El usuario no es un asesor activo"
                )
            assigned_advisor_id = advisor_id
        elif queue == OperationalQueue.UNASSIGNED:
            assigned_advisor_id = None
        elif actor.role == "ASESOR":
            assigned_advisor_id = actor.user.id
        elif advisor_id is not None:
            self._require_supervisor(actor)
            if not self.repository.advisor_exists(session, advisor_id):
                raise HTTPException(
                    status_code=422, detail="El usuario no es un asesor activo"
                )
            assigned_advisor_id = advisor_id
        self._validate_date_range(created_from, created_to)
        self._validate_date_range(updated_from, updated_to)
        total, items = self.repository.list_operational_page(
            session,
            queue=queue,
            page=page,
            page_size=page_size,
            now=datetime.now(UTC),
            assigned_advisor_id=assigned_advisor_id,
            status=status_filter,
            category_id=category_id,
            priority=priority,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
            updated_to=updated_to,
            source=source,
            search=search.strip() if search else None,
            recent_hours=recent_hours,
        )
        return PaginationResult(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
            items=items,
        )

    def _list(
        self,
        session: Session,
        *,
        page: int | None,
        page_size: int | None,
        client_id: str | None,
        status: TicketStatus | None,
        category_id: str | None,
        priority: TicketPriority | None,
        created_from: datetime | None,
        created_to: datetime | None,
        source: TicketSource | None,
        assigned_advisor_id: str | None,
        search: str | None,
        updated_from: datetime | None,
        updated_to: datetime | None,
    ) -> list[Ticket] | PaginationResult[Ticket]:
        self._validate_date_range(created_from, created_to)
        normalized_search = search.strip() if search else None
        if page is None and page_size is None:
            return self.repository.list_tickets(
                session,
                client_id=client_id,
                status=status,
                category_id=category_id,
                priority=priority,
                created_from=created_from,
                created_to=created_to,
                source=source,
                assigned_advisor_id=assigned_advisor_id,
                search=normalized_search,
                updated_from=updated_from,
                updated_to=updated_to,
            )

        current_page = page or 1
        current_page_size = page_size or 20
        total, items = self.repository.list_tickets_page(
            session,
            page=current_page,
            page_size=current_page_size,
            client_id=client_id,
            status=status,
            category_id=category_id,
            priority=priority,
            created_from=created_from,
            created_to=created_to,
            source=source,
            assigned_advisor_id=assigned_advisor_id,
            search=normalized_search,
            updated_from=updated_from,
            updated_to=updated_to,
        )
        return PaginationResult(
            page=current_page,
            page_size=current_page_size,
            total=total,
            total_pages=ceil(total / current_page_size) if total else 0,
            items=items,
        )

    @staticmethod
    def _validate_date_range(
        created_from: datetime | None, created_to: datetime | None
    ) -> None:
        if (
            created_from is not None
            and created_to is not None
            and created_from > created_to
        ):
            raise HTTPException(
                status_code=422,
                detail="created_from debe ser menor o igual que created_to",
            )

    def get(self, session: Session, ticket_id: str, actor: AuthenticatedUser) -> Ticket:
        ticket = self._get_authorized(session, ticket_id, actor)
        return ticket

    def add_comment(
        self,
        session: Session,
        ticket_id: str,
        data: CommentCreate,
        actor: AuthenticatedUser,
    ) -> TicketComment:
        ticket = self._get_authorized(
            session,
            ticket_id,
            actor,
            expected_version=data.expected_version,
            lock=True,
        )
        if ticket.status in {TicketStatus.CERRADO, TicketStatus.CANCELADO}:
            raise HTTPException(
                status_code=409, detail="El ticket no puede modificarse"
            )
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(
                status_code=403, detail="El ticket no está asignado a ti"
            )
        comment = self.repository.add_comment(
            session,
            TicketComment(
                ticket_id=ticket.id, author_id=actor.user.id, content=data.content
            ),
        )
        self._history(
            session,
            ticket.id,
            actor,
            "COMMENT_ADDED",
            None,
            None,
            "Comentario agregado",
        )
        self.sla.record_first_response(
            session,
            ticket.id,
            actor.user.id,
            actor.role,
            comment.created_at,
        )
        self._touch(ticket)
        recipient_id = (
            ticket.assigned_advisor_id if actor.role == "CLIENTE" else ticket.client_id
        )
        if recipient_id is not None and recipient_id != actor.user.id:
            self.notifications.create(
                session,
                recipient_user_id=recipient_id,
                notification_type=NotificationType.TICKET_COMMENTED,
                title="Nuevo comentario en tu ticket",
                message="Se agregó un nuevo comentario en un ticket que sigues.",
                related_ticket_id=ticket.id,
                metadata={"author_role": actor.role},
                dedupe_key=f"ticket_comment:{comment.id}",
            )
        session.commit()
        session.refresh(comment)
        return comment

    def comments(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> list[TicketComment]:
        self._get_authorized(session, ticket_id, actor)
        return self.repository.list_comments(session, ticket_id)

    def assign(
        self,
        session: Session,
        ticket_id: str,
        data: AssignmentCreate,
        actor: AuthenticatedUser,
    ) -> Ticket:
        self._require_supervisor(actor)
        ticket = self._get_mutable_ticket(session, ticket_id, data.expected_version)
        self._ensure_open(ticket)
        if not self.repository.advisor_exists(session, data.advisor_id):
            raise HTTPException(
                status_code=422, detail="El usuario no es un asesor activo"
            )
        old = ticket.assigned_advisor_id
        if old == data.advisor_id:
            raise HTTPException(
                status_code=409, detail="El ticket ya está asignado a ese asesor"
            )
        current_assignment = self.repository.current_assignment(session, ticket.id)
        now = datetime.now(UTC)
        if current_assignment is not None:
            self.repository.close_assignment(current_assignment, now)
            session.add(current_assignment)
        ticket.assigned_advisor_id = data.advisor_id
        ticket.assigned_at = now
        if ticket.status == TicketStatus.NUEVO:
            ticket.status = TicketStatus.ASIGNADO
        assignment = self.repository.add_assignment(
            session,
            TicketAssignment(
                ticket_id=ticket.id,
                advisor_id=data.advisor_id,
                assigned_by=actor.user.id,
                assigned_at=now,
            ),
        )
        self._touch(ticket, now)
        self._history(
            session,
            ticket.id,
            actor,
            "ASSIGNED",
            old,
            data.advisor_id,
            "Ticket asignado a un asesor",
        )
        self.notifications.create(
            session,
            recipient_user_id=data.advisor_id,
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Ticket asignado",
            message="Se te asignó un nuevo ticket para atención.",
            related_ticket_id=ticket.id,
            metadata={"assigned_by_role": actor.role},
            dedupe_key=f"ticket_assigned:{assignment.id}",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def take(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        expected_version: int | None = None,
    ) -> Ticket:
        if actor.role != "ASESOR":
            raise HTTPException(
                status_code=403, detail="Solo un asesor puede tomar tickets"
            )
        ticket = self._get_mutable_ticket(session, ticket_id, expected_version)
        self._ensure_open(ticket)
        if ticket.assigned_advisor_id is not None:
            raise HTTPException(status_code=409, detail="El ticket ya está asignado")
        now = datetime.now(UTC)
        ticket.assigned_advisor_id = actor.user.id
        ticket.assigned_at = now
        if ticket.status == TicketStatus.NUEVO:
            ticket.status = TicketStatus.ASIGNADO
        assignment = self.repository.add_assignment(
            session,
            TicketAssignment(
                ticket_id=ticket.id,
                advisor_id=actor.user.id,
                assigned_by=actor.user.id,
                assigned_at=now,
            ),
        )
        self._touch(ticket, now)
        self._history(
            session,
            ticket.id,
            actor,
            "TAKEN",
            None,
            actor.user.id,
            "Ticket tomado por el asesor autenticado",
        )
        self.notifications.create(
            session,
            recipient_user_id=actor.user.id,
            notification_type=NotificationType.TICKET_ASSIGNED,
            title="Ticket tomado",
            message="Tomaste un ticket para atención.",
            related_ticket_id=ticket.id,
            metadata={"assigned_by_role": actor.role},
            dedupe_key=f"ticket_assigned:{assignment.id}",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def release(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        expected_version: int | None = None,
    ) -> Ticket:
        self._require_staff(actor)
        ticket = self._get_mutable_ticket(session, ticket_id, expected_version)
        self._ensure_open(ticket)
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(
                status_code=403, detail="El ticket no está asignado a ti"
            )
        if ticket.assigned_advisor_id is None:
            raise HTTPException(status_code=409, detail="El ticket no está asignado")
        old = ticket.assigned_advisor_id
        assignment = self.repository.current_assignment(session, ticket.id)
        now = datetime.now(UTC)
        if assignment is not None:
            self.repository.close_assignment(assignment, now)
            session.add(assignment)
        ticket.assigned_advisor_id = None
        ticket.assigned_at = None
        if ticket.status == TicketStatus.ASIGNADO:
            ticket.status = TicketStatus.NUEVO
        self._touch(ticket, now)
        self._history(
            session,
            ticket.id,
            actor,
            "RELEASED",
            old,
            None,
            "Ticket liberado de la bandeja del asesor",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def change_status(
        self,
        session: Session,
        ticket_id: str,
        data: StatusChange,
        actor: AuthenticatedUser,
        *,
        notification_type: NotificationType = NotificationType.TICKET_STATUS_CHANGED,
    ) -> Ticket:
        self._require_staff(actor)
        ticket = self._get_mutable_ticket(session, ticket_id, data.expected_version)
        self._ensure_actor_can_manage(ticket, actor)
        self._transition(ticket, data.status, data.reason, actor.role)
        old = ticket.status
        ticket.status = data.status
        self._set_status_timestamp(ticket, data.status)
        self._touch(ticket)
        self.sla.on_status_change(session, ticket, old, data.status, actor.user.id)
        history = self._history(
            session,
            ticket.id,
            actor,
            "STATUS_CHANGED",
            old,
            data.status,
            data.reason or "Estado actualizado",
        )
        self.notifications.create(
            session,
            recipient_user_id=ticket.client_id,
            notification_type=notification_type,
            title="Estado de ticket actualizado",
            message="El estado de tu ticket fue actualizado.",
            related_ticket_id=ticket.id,
            metadata={"old_status": old.value, "new_status": data.status.value},
            dedupe_key=f"ticket_status:{history.id}:{notification_type.value}",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def cancel(
        self,
        session: Session,
        ticket_id: str,
        data: ReasonRequest,
        actor: AuthenticatedUser,
    ) -> Ticket:
        self._require_supervisor(actor)
        return self.change_status(
            session,
            ticket_id,
            StatusChange(
                status=TicketStatus.CANCELADO,
                reason=data.reason,
                expected_version=data.expected_version,
            ),
            actor,
        )

    def reopen(
        self,
        session: Session,
        ticket_id: str,
        data: ReasonRequest,
        actor: AuthenticatedUser,
    ) -> Ticket:
        self._require_staff(actor)
        ticket = self._get_ticket(session, ticket_id)
        self._ensure_actor_can_manage(ticket, actor)
        if ticket.status != TicketStatus.RESUELTO:
            raise HTTPException(
                status_code=409, detail="Solo un ticket resuelto puede reabrirse"
            )
        return self.change_status(
            session,
            ticket_id,
            StatusChange(
                status=TicketStatus.EN_PROCESO,
                reason=data.reason,
                expected_version=data.expected_version,
            ),
            actor,
            notification_type=NotificationType.TICKET_REOPENED,
        )

    def close(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        self._require_staff(actor)
        return self.change_status(
            session,
            ticket_id,
            StatusChange(status=TicketStatus.CERRADO),
            actor,
            notification_type=NotificationType.TICKET_CLOSED,
        )

    def history(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> list[TicketHistory]:
        self._get_authorized(session, ticket_id, actor)
        return self.repository.history(session, ticket_id)

    def _create(
        self,
        session: Session,
        data: TicketCreate,
        client_id: str,
        actor: AuthenticatedUser,
        source: TicketSource,
        conversation_id: str | None = None,
    ) -> Ticket:
        if not self.repository.category_exists(session, data.category_id):
            raise HTTPException(
                status_code=422, detail="La categoría no existe o está inactiva"
            )
        ticket = Ticket(
            tracking_code=f"TCK-{datetime.now(UTC):%Y%m%d}-{uuid4().hex[:10].upper()}",
            client_id=client_id,
            conversation_id=conversation_id,
            category_id=data.category_id,
            subject=data.subject.strip(),
            description=data.description.strip(),
            priority=data.priority,
            source=source,
        )
        self.repository.add(session, ticket)
        self.sla.start_for_ticket(session, ticket, actor.user.id)
        self._history(
            session,
            ticket.id,
            actor,
            "CREATED",
            None,
            TicketStatus.NUEVO,
            "Ticket creado",
        )
        self.notifications.create(
            session,
            recipient_user_id=client_id,
            notification_type=NotificationType.TICKET_CREATED,
            title="Ticket creado",
            message="Tu ticket fue creado correctamente.",
            related_ticket_id=ticket.id,
            related_conversation_id=conversation_id,
            metadata={"source": source.value},
            dedupe_key=f"ticket_created:{ticket.id}",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def _get_authorized(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        *,
        expected_version: int | None = None,
        lock: bool = False,
    ) -> Ticket:
        ticket = (
            self._get_mutable_ticket(session, ticket_id, expected_version)
            if lock
            else self._get_ticket(session, ticket_id)
        )
        if actor.role == "CLIENTE" and ticket.client_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role not in {"CLIENTE", "ASESOR", "SUPERVISOR"}:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        return ticket

    def _get_mutable_ticket(
        self, session: Session, ticket_id: str, expected_version: int | None
    ) -> Ticket:
        ticket = self.repository.get_for_update(session, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        if expected_version is not None and ticket.version != expected_version:
            raise HTTPException(
                status_code=409,
                detail="El ticket fue actualizado por otra operación",
            )
        return ticket

    def _get_ticket(self, session: Session, ticket_id: str) -> Ticket:
        ticket = self.repository.get(session, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        return ticket

    @staticmethod
    def _require_client(actor: AuthenticatedUser) -> None:
        if actor.role != "CLIENTE":
            raise HTTPException(
                status_code=403, detail="Solo un cliente puede crear tickets"
            )

    @staticmethod
    def _require_staff(actor: AuthenticatedUser) -> None:
        if actor.role not in {"ASESOR", "SUPERVISOR"}:
            raise HTTPException(status_code=403, detail="Se requiere un rol interno")

    @staticmethod
    def _require_supervisor(actor: AuthenticatedUser) -> None:
        if actor.role != "SUPERVISOR":
            raise HTTPException(
                status_code=403,
                detail="Solo un supervisor puede realizar esta operación",
            )

    @staticmethod
    def _ensure_open(ticket: Ticket) -> None:
        if ticket.status in {TicketStatus.CERRADO, TicketStatus.CANCELADO}:
            raise HTTPException(
                status_code=409, detail="El ticket no puede modificarse"
            )

    @staticmethod
    def _ensure_actor_can_manage(ticket: Ticket, actor: AuthenticatedUser) -> None:
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(
                status_code=403, detail="El ticket no está asignado a ti"
            )

    @staticmethod
    def _transition(
        ticket: Ticket,
        new_status: TicketStatus,
        reason: str | None,
        actor_role: str,
    ) -> None:
        if ticket.status in {TicketStatus.CERRADO, TicketStatus.CANCELADO}:
            raise HTTPException(
                status_code=409, detail="El ticket no puede modificarse"
            )
        if new_status == TicketStatus.CANCELADO and not reason:
            raise HTTPException(
                status_code=422, detail="La cancelación requiere un motivo"
            )
        if new_status == TicketStatus.CANCELADO and actor_role != "SUPERVISOR":
            raise HTTPException(
                status_code=403,
                detail="Solo un supervisor puede cancelar tickets",
            )
        if (
            ticket.status == TicketStatus.RESUELTO
            and new_status == TicketStatus.EN_PROCESO
            and not reason
        ):
            raise HTTPException(
                status_code=422,
                detail="La reapertura requiere un motivo",
            )
        allowed = ALLOWED_TRANSITIONS[ticket.status]
        if new_status not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Transición inválida: {ticket.status} → {new_status}",
            )

    @staticmethod
    def _set_status_timestamp(ticket: Ticket, new_status: TicketStatus) -> None:
        now = datetime.now(UTC)
        if new_status == TicketStatus.RESUELTO:
            ticket.resolved_at = now
        elif new_status == TicketStatus.CERRADO:
            ticket.closed_at = now
        elif new_status == TicketStatus.CANCELADO:
            ticket.cancelled_at = now

    @staticmethod
    def _touch(ticket: Ticket, now: datetime | None = None) -> None:
        ticket.version += 1
        ticket.updated_at = now or datetime.now(UTC)

    def _history(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        action: str,
        old: object,
        new: object,
        description: str,
    ) -> TicketHistory:
        history = self.repository.add_history(
            session,
            TicketHistory(
                ticket_id=ticket_id,
                actor_id=actor.user.id,
                action=action,
                old_value=str(old) if old is not None else None,
                new_value=str(new) if new is not None else None,
                description=description,
            ),
        )
        self.audit.record(
            session,
            event_type="TICKET",
            action=action,
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="TICKET",
            resource_id=ticket_id,
            success=True,
            before_data={"value": old},
            after_data={"value": new},
            metadata={"description": description},
        )
        return history
