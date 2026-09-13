from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
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
from app.modules.tickets.schemas.ticket import (
    AssignmentCreate,
    CommentCreate,
    ReasonRequest,
    StatusChange,
    TicketCreate,
)

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
    def __init__(self, repository: TicketRepository | None = None) -> None:
        self.repository = repository or TicketRepository()

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
        if self.repository.get_by_conversation(session, conversation_id) is not None:
            raise HTTPException(
                status_code=409, detail="La conversación ya tiene un ticket"
            )
        bot_message = self.repository.last_bot_message(session, conversation_id)
        if bot_message is None or bot_message.confidence != 0:
            raise HTTPException(
                status_code=409,
                detail="Solo una conversación no resuelta puede convertirse en ticket",
            )
        return self._create(
            session, data, actor.user.id, actor, TicketSource.CHATBOT, conversation_id
        )

    def list_mine(self, session: Session, actor: AuthenticatedUser) -> list[Ticket]:
        self._require_client(actor)
        return self.repository.list_tickets(session, client_id=actor.user.id)

    def list_global(
        self,
        session: Session,
        actor: AuthenticatedUser,
        status_filter: TicketStatus | None,
        category_id: str | None,
        priority: TicketPriority | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> list[Ticket]:
        self._require_staff(actor)
        return self.repository.list_tickets(
            session,
            status=status_filter,
            category_id=category_id,
            priority=priority,
            created_from=created_from,
            created_to=created_to,
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
        ticket = self._get_authorized(session, ticket_id, actor)
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
        ticket = self._get_ticket(session, ticket_id)
        self._ensure_open(ticket)
        if not self.repository.advisor_exists(session, data.advisor_id):
            raise HTTPException(
                status_code=422, detail="El usuario no es un asesor activo"
            )
        old = ticket.assigned_advisor_id
        ticket.assigned_advisor_id = data.advisor_id
        ticket.assigned_at = datetime.now(UTC)
        if ticket.status == TicketStatus.NUEVO:
            ticket.status = TicketStatus.ASIGNADO
        self.repository.add_assignment(
            session,
            TicketAssignment(
                ticket_id=ticket.id,
                advisor_id=data.advisor_id,
                assigned_by=actor.user.id,
            ),
        )
        self._history(
            session,
            ticket.id,
            actor,
            "ASSIGNED",
            old,
            data.advisor_id,
            "Ticket asignado a un asesor",
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
    ) -> Ticket:
        self._require_staff(actor)
        ticket = self._get_ticket(session, ticket_id)
        self._ensure_actor_can_manage(ticket, actor)
        self._transition(ticket, data.status, data.reason)
        old = ticket.status
        ticket.status = data.status
        self._set_status_timestamp(ticket, data.status)
        self._history(
            session,
            ticket.id,
            actor,
            "STATUS_CHANGED",
            old,
            data.status,
            data.reason or "Estado actualizado",
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
            StatusChange(status=TicketStatus.CANCELADO, reason=data.reason),
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
            StatusChange(status=TicketStatus.EN_PROCESO, reason=data.reason),
            actor,
        )

    def close(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        self._require_staff(actor)
        return self.change_status(
            session, ticket_id, StatusChange(status=TicketStatus.CERRADO), actor
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
        self._history(
            session,
            ticket.id,
            actor,
            "CREATED",
            None,
            TicketStatus.NUEVO,
            "Ticket creado",
        )
        session.commit()
        session.refresh(ticket)
        return ticket

    def _get_authorized(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        ticket = self._get_ticket(session, ticket_id)
        if actor.role == "CLIENTE" and ticket.client_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
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
        ticket: Ticket, new_status: TicketStatus, reason: str | None
    ) -> None:
        if ticket.status in {TicketStatus.CERRADO, TicketStatus.CANCELADO}:
            raise HTTPException(
                status_code=409, detail="El ticket no puede modificarse"
            )
        if new_status == TicketStatus.CANCELADO and not reason:
            raise HTTPException(
                status_code=422, detail="La cancelación requiere un motivo"
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

    def _history(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        action: str,
        old: object,
        new: object,
        description: str,
    ) -> None:
        self.repository.add_history(
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
