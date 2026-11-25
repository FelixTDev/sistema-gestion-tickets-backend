from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.chatbot.models.conversation import (
    TICKET_DRAFT_CONTEXT_KEY,
    Conversation,
    ConversationStatus,
)
from app.modules.chatbot.schemas.chatbot import (
    ChatbotTicketDraft,
    ChatbotTicketDraftStatus,
)
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
from app.modules.tickets.schemas.operations import (
    OperationalQueue,
    TicketReleaseRequest,
)
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
from app.modules.tickets.services.sla_service import SlaService
from app.shared.datetime import as_utc
from app.shared.exceptions import AppError
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

REQUIRES_ASSIGNED_ADVISOR = frozenset(
    {
        TicketStatus.ASIGNADO,
        TicketStatus.EN_PROCESO,
        TicketStatus.PENDIENTE_CLIENTE,
        TicketStatus.RESUELTO,
    }
)

# These action codes are written by application paths that use TicketHistory's
# UTC default, explicit UTC seed times, or the UTC assignment-repair migration.
# Unknown codes may come from historical imports, whose timezone cannot be inferred.
_KNOWN_UTC_HISTORY_ACTIONS = frozenset(
    {
        "CREATED",
        "COMMENT_ADDED",
        "ASSIGNED",
        "TAKEN",
        "RELEASED",
        "STATUS_CHANGED",
        "CANCELLED",
        "SLA_STARTED",
        "SLA_FIRST_RESPONSE",
        "SLA_REOPENED",
        "SLA_STATUS_CHANGED",
        "SLA_PAUSED",
        "SLA_RESUMED",
        "SLA_WARNING",
        "SLA_BREACHED",
        "ATTACHMENT_UPLOADED",
        "ATTACHMENT_DELETED",
        "SYSTEM_ASSIGNMENT_REPAIR",
    }
)
_TICKET_CREATION_HISTORY_DESCRIPTIONS = frozenset(
    {
        "Ticket creado",
        "Ticket creado desde el canal de atención correspondiente.",
    }
)
_ASSIGNMENT_HISTORY_ACTIONS = frozenset({"ASSIGNED", "TAKEN"})
_STATUS_TIMESTAMP_VALUES = {
    "RESUELTO": "resolved_at",
    "CERRADO": "closed_at",
    "CANCELADO": "cancelled_at",
}
_SYSTEM_ASSIGNMENT_REPAIR_DESCRIPTIONS = frozenset(
    {
        "Reparación defensiva: el estado requería asesor, pero el ticket no tenía "
        "un asesor asignado.",
        "Reparación defensiva: un ticket NUEVO no puede conservar asesor ni marcas "
        "temporales de asignación o cierre.",
    }
)
_SYSTEM_SLA_HISTORY_SIGNATURES = {
    "SLA_WARNING": ("first_response", "resolution", "Advertencia SLA emitida"),
    "SLA_BREACHED": (
        "first_response",
        "resolution",
        "Incumplimiento SLA registrado",
    ),
}


@dataclass(frozen=True)
class TicketConversionResult:
    ticket: TicketRead
    created: bool


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
    ) -> TicketRead:
        self._require_client(actor)
        ticket = self._create(session, data, actor.user.id, actor, TicketSource.MANUAL)
        return self._ticket_read(
            session,
            ticket,
            known_utc_fields=frozenset({"created_at", "updated_at"}),
        )

    def convert_conversation(
        self,
        session: Session,
        conversation_id: str,
        data: TicketCreate,
        actor: AuthenticatedUser,
    ) -> TicketConversionResult:
        self._require_client(actor)
        conversation = self.repository.conversation_for_update(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        if conversation.user_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Conversación no autorizada")
        existing_ticket = self.repository.get_by_conversation(session, conversation_id)
        if existing_ticket is not None:
            return self._recover_conversation_ticket(
                session, existing_ticket, data, actor
            )
        if conversation.status in {
            ConversationStatus.CONVERTED_TO_TICKET,
            ConversationStatus.CLOSED,
            ConversationStatus.EXPIRED,
        }:
            raise HTTPException(
                status_code=409, detail="La conversación no está disponible"
            )
        self._validate_conversation_draft(conversation, data)
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
        if bot_message is None:
            raise HTTPException(
                status_code=409,
                detail="La conversación aún no tiene una respuesta para convertir",
            )
        try:
            ticket = self._create(
                session,
                data,
                actor.user.id,
                actor,
                TicketSource.CHATBOT,
                conversation_id,
                commit=False,
            )
        except IntegrityError as error:
            session.rollback()
            return self._recover_after_conversion_conflict(
                session,
                conversation_id=conversation_id,
                data=data,
                actor=actor,
                original_error=error,
            )
        now = datetime.now(UTC)
        conversation.status = ConversationStatus.CONVERTED_TO_TICKET
        conversation.converted_at = now
        conversation.ended_at = now
        conversation.last_activity_at = now
        context = dict(conversation.context_json or {})
        advisor_handoff = context.get("advisor_handoff_requested") is True
        if advisor_handoff:
            conversation.escalation_reason = "CLIENT_REQUESTED_ADVISOR"
            conversation.escalated_at = now
        context.pop(TICKET_DRAFT_CONTEXT_KEY, None)
        context.pop("advisor_summary", None)
        conversation.context_json = context
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
            metadata={
                "ticket_id": ticket.id,
                "source": TicketSource.CHATBOT.value,
                "advisor_handoff": advisor_handoff,
            },
        )
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            return self._recover_after_conversion_conflict(
                session,
                conversation_id=conversation_id,
                data=data,
                actor=actor,
                original_error=error,
            )
        session.refresh(ticket)
        return TicketConversionResult(
            ticket=self._ticket_read(
                session,
                ticket,
                known_utc_fields=frozenset({"created_at", "updated_at"}),
            ),
            created=True,
        )

    def _recover_after_conversion_conflict(
        self,
        session: Session,
        *,
        conversation_id: str,
        data: TicketCreate,
        actor: AuthenticatedUser,
        original_error: IntegrityError,
    ) -> TicketConversionResult:
        recovered_conversation = self.repository.conversation_for_update(
            session, conversation_id
        )
        if recovered_conversation is None:
            raise HTTPException(
                status_code=404, detail="Conversación no encontrada"
            ) from None
        if recovered_conversation.user_id != actor.user.id:
            raise HTTPException(
                status_code=403, detail="Conversación no autorizada"
            ) from None
        existing_ticket = self.repository.get_by_conversation(session, conversation_id)
        if existing_ticket is not None:
            return self._recover_conversation_ticket(
                session, existing_ticket, data, actor
            )
        raise original_error

    def _recover_conversation_ticket(
        self,
        session: Session,
        ticket: Ticket,
        data: TicketCreate,
        actor: AuthenticatedUser,
    ) -> TicketConversionResult:
        if ticket.source != TicketSource.CHATBOT or ticket.client_id != actor.user.id:
            raise HTTPException(
                status_code=409,
                detail="La conversación tiene un ticket no recuperable",
            )
        payload_matches = (
            ticket.category_id == data.category_id
            and ticket.subject == data.subject.strip()
            and ticket.description == data.description.strip()
            and ticket.priority == data.priority
        )
        if not payload_matches:
            raise HTTPException(
                status_code=409,
                detail=(
                    "El payload no coincide con el ticket creado para la conversación"
                ),
            )
        return TicketConversionResult(
            ticket=self._ticket_read(session, ticket), created=False
        )

    @staticmethod
    def _validate_conversation_draft(
        conversation: Conversation, data: TicketCreate
    ) -> None:
        raw_draft = (conversation.context_json or {}).get(TICKET_DRAFT_CONTEXT_KEY)
        if not isinstance(raw_draft, dict):
            return
        try:
            draft = ChatbotTicketDraft.model_validate(raw_draft)
        except ValueError:
            raise HTTPException(
                status_code=409, detail="El borrador de ticket no es válido"
            ) from None
        if draft.status == ChatbotTicketDraftStatus.INACTIVE:
            return
        if draft.status != ChatbotTicketDraftStatus.CONFIRMED:
            raise HTTPException(
                status_code=409,
                detail="Confirma explícitamente el resumen antes de convertirlo",
            )
        fields = draft.fields
        if draft.missing_fields or draft.validation_errors:
            raise HTTPException(
                status_code=409, detail="El borrador de ticket está incompleto"
            )
        if (
            fields.category_id != data.category_id
            or fields.subject != data.subject.strip()
            or fields.description != data.description.strip()
            or fields.priority != data.priority
        ):
            raise HTTPException(
                status_code=409,
                detail="Los datos confirmados no coinciden con el resumen del ticket",
            )

    def list_mine(
        self,
        session: Session,
        actor: AuthenticatedUser,
        *,
        page: int | None = None,
        page_size: int | None = None,
        status_filter: TicketStatus | None = None,
        statuses: set[TicketStatus] | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        source: TicketSource | None = None,
        assigned_advisor_id: str | None = None,
        search: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[TicketRead] | PaginationResult[TicketRead]:
        self._require_client(actor)
        return self._list(
            session,
            page=page,
            page_size=page_size,
            client_id=actor.user.id,
            status=status_filter,
            statuses=statuses,
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
    ) -> list[TicketRead] | PaginationResult[TicketRead]:
        self._require_staff(actor)
        if actor.role == "ASESOR":
            assigned_advisor_id = actor.user.id
        return self._list(
            session,
            page=page,
            page_size=page_size,
            client_id=client_id,
            status=status_filter,
            statuses=None,
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
    ) -> PaginationResult[TicketRead]:
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
            items=self._ticket_reads(session, items),
        )

    def _list(
        self,
        session: Session,
        *,
        page: int | None,
        page_size: int | None,
        client_id: str | None,
        status: TicketStatus | None,
        statuses: set[TicketStatus] | None,
        category_id: str | None,
        priority: TicketPriority | None,
        created_from: datetime | None,
        created_to: datetime | None,
        source: TicketSource | None,
        assigned_advisor_id: str | None,
        search: str | None,
        updated_from: datetime | None,
        updated_to: datetime | None,
    ) -> list[TicketRead] | PaginationResult[TicketRead]:
        self._validate_date_range(created_from, created_to)
        normalized_search = search.strip() if search else None
        if page is None and page_size is None:
            tickets = self.repository.list_tickets(
                session,
                client_id=client_id,
                status=status,
                statuses=statuses,
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
            return self._ticket_reads(session, tickets)

        current_page = page or 1
        current_page_size = page_size or 20
        total, items = self.repository.list_tickets_page(
            session,
            page=current_page,
            page_size=current_page_size,
            client_id=client_id,
            status=status,
            statuses=statuses,
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
            items=self._ticket_reads(session, items),
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

    def get(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> TicketRead:
        ticket = self._get_authorized(session, ticket_id, actor)
        return self._ticket_read(session, ticket)

    def get_by_tracking_code(
        self, session: Session, tracking_code: str, actor: AuthenticatedUser
    ) -> TicketRead:
        ticket = self.repository.get_by_tracking_code(session, tracking_code)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        authorized_ticket = self._get_authorized(session, ticket.id, actor)
        return self._ticket_read(session, authorized_ticket)

    def add_comment(
        self,
        session: Session,
        ticket_id: str,
        data: CommentCreate,
        actor: AuthenticatedUser,
    ) -> CommentRead:
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
        return self._comment_reads(session, [comment])[0]

    def comments(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> list[CommentRead]:
        self._get_authorized(session, ticket_id, actor)
        comments = self.repository.list_comments(session, ticket_id)
        return self._comment_reads(session, comments)

    def assign(
        self,
        session: Session,
        ticket_id: str,
        data: AssignmentCreate,
        actor: AuthenticatedUser,
    ) -> TicketRead:
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
        return self._ticket_read(
            session,
            ticket,
            known_utc_fields=frozenset({"updated_at", "assigned_at"}),
        )

    def take(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        expected_version: int | None = None,
    ) -> TicketRead:
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
        return self._ticket_read(
            session,
            ticket,
            known_utc_fields=frozenset({"updated_at", "assigned_at"}),
        )

    def release(
        self,
        session: Session,
        ticket_id: str,
        data: TicketReleaseRequest,
        actor: AuthenticatedUser,
    ) -> TicketRead:
        self._require_staff(actor)
        ticket = self._get_mutable_ticket(session, ticket_id, data.expected_version)
        self._ensure_open(ticket)
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(
                status_code=403, detail="El ticket no está asignado a ti"
            )
        if ticket.assigned_advisor_id is None:
            raise HTTPException(status_code=409, detail="El ticket no está asignado")
        old = ticket.assigned_advisor_id
        old_status = ticket.status
        assignment = self.repository.current_assignment(session, ticket.id)
        now = datetime.now(UTC)
        if assignment is not None:
            self.repository.close_assignment(assignment, now)
            session.add(assignment)
        ticket.assigned_advisor_id = None
        ticket.assigned_at = None
        ticket.status = TicketStatus.NUEVO
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.cancelled_at = None
        self._touch(ticket, now)
        self._history(
            session,
            ticket.id,
            actor,
            "RELEASED",
            old,
            None,
            data.reason,
        )
        self._history(
            session,
            ticket.id,
            actor,
            "STATUS_CHANGED",
            old_status,
            TicketStatus.NUEVO,
            f"Estado cambiado de {old_status.value} a {TicketStatus.NUEVO.value}",
        )
        session.commit()
        session.refresh(ticket)
        return self._ticket_read(
            session, ticket, known_utc_fields=frozenset({"updated_at"})
        )

    def change_status(
        self,
        session: Session,
        ticket_id: str,
        data: StatusChange,
        actor: AuthenticatedUser,
        *,
        notification_type: NotificationType = NotificationType.TICKET_STATUS_CHANGED,
    ) -> TicketRead:
        self._require_staff(actor)
        ticket = self._get_mutable_ticket(session, ticket_id, data.expected_version)
        self._ensure_actor_can_manage(ticket, actor)
        return self._apply_status_change(
            session, ticket, data, actor, notification_type=notification_type
        )

    def confirm_resolution(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
    ) -> TicketRead:
        if actor.role != "CLIENTE":
            raise HTTPException(
                status_code=403,
                detail="Solo el cliente propietario puede confirmar el cierre",
            )
        ticket = self._get_authorized(session, ticket_id, actor, lock=True)
        if ticket.status != TicketStatus.RESUELTO:
            raise HTTPException(
                status_code=409,
                detail="Solo un ticket resuelto puede confirmarse como cerrado",
            )
        return self._apply_status_change(
            session,
            ticket,
            StatusChange(status=TicketStatus.CERRADO),
            actor,
            notification_type=NotificationType.TICKET_CLOSED,
        )

    def _apply_status_change(
        self,
        session: Session,
        ticket: Ticket,
        data: StatusChange,
        actor: AuthenticatedUser,
        *,
        notification_type: NotificationType,
    ) -> TicketRead:
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
        known_utc_fields = {"updated_at"}
        timestamp_field = _STATUS_TIMESTAMP_VALUES.get(data.status.value)
        if timestamp_field is not None:
            known_utc_fields.add(timestamp_field)
        return self._ticket_read(
            session, ticket, known_utc_fields=frozenset(known_utc_fields)
        )

    def cancel(
        self,
        session: Session,
        ticket_id: str,
        data: ReasonRequest,
        actor: AuthenticatedUser,
    ) -> TicketRead:
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
    ) -> TicketRead:
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
    ) -> TicketRead:
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
    ) -> list[HistoryRead]:
        self._get_authorized(session, ticket_id, actor)
        history = self.repository.history(session, ticket_id)
        identities = self.repository.user_identities(
            session, {item.actor_id for item in history if item.actor_id is not None}
        )
        return [
            HistoryRead(
                id=item.id,
                ticket_id=item.ticket_id,
                actor_id=item.actor_id,
                actor_name=(
                    identities[item.actor_id][0]
                    if item.actor_id in identities
                    else None
                ),
                actor_role=(
                    identities[item.actor_id][1]
                    if item.actor_id in identities
                    else None
                ),
                action=item.action,
                old_value=item.old_value,
                new_value=item.new_value,
                description=item.description,
                created_at=self._history_read_timestamp(item),
            )
            for item in history
        ]

    @staticmethod
    def _history_read_timestamp(item: TicketHistory) -> datetime:
        if item.created_at.tzinfo is not None:
            return as_utc(item.created_at)
        if item.action == "CREATED":
            is_known_creation = (
                item.actor_id is not None
                and item.old_value is None
                and item.new_value == TicketStatus.NUEVO.value
                and item.description in _TICKET_CREATION_HISTORY_DESCRIPTIONS
            )
            return as_utc(item.created_at) if is_known_creation else item.created_at
        if item.action == "SYSTEM_ASSIGNMENT_REPAIR":
            is_known_repair = (
                item.actor_id is None
                and item.new_value == TicketStatus.NUEVO.value
                and item.description in _SYSTEM_ASSIGNMENT_REPAIR_DESCRIPTIONS
            )
            return as_utc(item.created_at) if is_known_repair else item.created_at
        system_sla_signature = _SYSTEM_SLA_HISTORY_SIGNATURES.get(item.action)
        if system_sla_signature is not None:
            is_known_system_event = (
                item.actor_id is None
                and item.new_value in system_sla_signature[:2]
                and item.description == system_sla_signature[2]
            )
            return as_utc(item.created_at) if is_known_system_event else item.created_at
        if item.action in _KNOWN_UTC_HISTORY_ACTIONS and item.actor_id is not None:
            return as_utc(item.created_at)
        return item.created_at

    def _ticket_read(
        self,
        session: Session,
        ticket: Ticket,
        *,
        known_utc_fields: frozenset[str] = frozenset(),
    ) -> TicketRead:
        return self._ticket_reads(session, [ticket], known_utc_fields=known_utc_fields)[
            0
        ]

    def _ticket_reads(
        self,
        session: Session,
        tickets: list[Ticket],
        *,
        known_utc_fields: frozenset[str] = frozenset(),
    ) -> list[TicketRead]:
        participants = self.repository.ticket_participant_names(
            session, [ticket.id for ticket in tickets]
        )
        needs_timestamp_provenance = any(
            getattr(ticket, field) is not None and field not in known_utc_fields
            for ticket in tickets
            for field in (
                "created_at",
                "updated_at",
                "assigned_at",
                "resolved_at",
                "closed_at",
                "cancelled_at",
            )
        )
        timestamp_history = (
            self.repository.ticket_timestamp_history(
                session, [ticket.id for ticket in tickets]
            )
            if needs_timestamp_provenance
            else {}
        )
        ticket_reads: list[TicketRead] = []
        for ticket in tickets:
            ticket_data = ticket.model_dump()
            ticket_known_utc_fields = set(known_utc_fields)
            creation_time_is_verified = any(
                action == "CREATED"
                and new_value == TicketStatus.NUEVO.value
                and self._same_mysql_second(ticket.created_at, history_created_at)
                for action, new_value, history_created_at in timestamp_history.get(
                    ticket.id, []
                )
            )
            if creation_time_is_verified:
                ticket_known_utc_fields.add("created_at")
            # Runtime _touch() and migration 0018 are the only version incrementers;
            # both write UTC. Require a matching history event as evidence of that
            # write; for version 1, only a matching new-ticket signature can separate
            # updated_at from values backfilled by migration 0009.
            update_time_is_verified = ticket.version > 1 and any(
                action != "CREATED"
                and self._same_mysql_second(ticket.updated_at, history_created_at)
                for action, _, history_created_at in timestamp_history.get(
                    ticket.id, []
                )
            )
            new_ticket_update_is_verified = creation_time_is_verified and (
                self._same_mysql_second(ticket.created_at, ticket.updated_at)
            )
            if update_time_is_verified or new_ticket_update_is_verified:
                ticket_known_utc_fields.add("updated_at")
            assigned_time_is_verified = ticket.assigned_at is not None and any(
                action in _ASSIGNMENT_HISTORY_ACTIONS
                and new_value == ticket.assigned_advisor_id
                and self._same_mysql_second(ticket.assigned_at, history_created_at)
                for action, new_value, history_created_at in timestamp_history.get(
                    ticket.id, []
                )
            )
            if assigned_time_is_verified:
                ticket_known_utc_fields.add("assigned_at")
            for status_value, timestamp_field in _STATUS_TIMESTAMP_VALUES.items():
                timestamp = getattr(ticket, timestamp_field)
                status_time_is_verified = timestamp is not None and any(
                    action == "STATUS_CHANGED"
                    and new_value == status_value
                    and self._same_mysql_second(timestamp, history_created_at)
                    for action, new_value, history_created_at in timestamp_history.get(
                        ticket.id, []
                    )
                )
                if status_time_is_verified:
                    ticket_known_utc_fields.add(timestamp_field)
            # MySQL strips tzinfo; attach UTC only when the write path is known.
            for field in (
                "created_at",
                "updated_at",
                "assigned_at",
                "resolved_at",
                "closed_at",
                "cancelled_at",
            ):
                value = ticket_data[field]
                if value is not None and (
                    field in ticket_known_utc_fields or value.tzinfo is not None
                ):
                    ticket_data[field] = as_utc(value)
            ticket_reads.append(
                TicketRead.model_validate(
                    ticket_data
                    | {
                        "client_name": participants[ticket.id][0],
                        "assigned_advisor_name": participants[ticket.id][1],
                    }
                )
            )
        return ticket_reads

    @staticmethod
    def _same_mysql_second(left: datetime, right: datetime) -> bool:
        left_offset = left.utcoffset()
        right_offset = right.utcoffset()
        if (left_offset is None) != (right_offset is None):
            return False
        if left_offset is not None and right_offset is not None:
            left = left.astimezone(UTC).replace(tzinfo=None)
            right = right.astimezone(UTC).replace(tzinfo=None)
        return left.replace(microsecond=0) == right.replace(microsecond=0)

    def _comment_reads(
        self, session: Session, comments: list[TicketComment]
    ) -> list[CommentRead]:
        identities = self.repository.user_identities(
            session, {comment.author_id for comment in comments}
        )
        return [
            CommentRead(
                id=comment.id,
                ticket_id=comment.ticket_id,
                author_id=comment.author_id,
                author_name=identities[comment.author_id][0],
                author_role=identities[comment.author_id][1],
                content=comment.content,
                created_at=comment.created_at,
            )
            for comment in comments
        ]

    def _create(
        self,
        session: Session,
        data: TicketCreate,
        client_id: str,
        actor: AuthenticatedUser,
        source: TicketSource,
        conversation_id: str | None = None,
        *,
        commit: bool = True,
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
        self._history(
            session,
            ticket.id,
            actor,
            "CREATED",
            None,
            TicketStatus.NUEVO,
            "Ticket creado",
        )
        self.sla.start_for_ticket(session, ticket, actor.user.id)
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
        if commit:
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
            new_status in REQUIRES_ASSIGNED_ADVISOR
            and ticket.assigned_advisor_id is None
        ):
            raise AppError(
                409,
                "TICKET_ASSIGNMENT_REQUIRED",
                "El ticket requiere un asesor asignado.",
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
