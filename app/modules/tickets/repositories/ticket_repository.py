from datetime import datetime, timedelta

from sqlalchemy import func, or_
from sqlmodel import Session, select

from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.assignment import TicketAssignment
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.sla import SlaPolicy, SlaStatus, TicketSla
from app.modules.tickets.models.ticket import (
    Ticket,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.modules.tickets.schemas.operations import OperationalQueue
from app.modules.usuarios.models.user import User


class TicketRepository:
    def get(self, session: Session, ticket_id: str) -> Ticket | None:
        return session.get(Ticket, ticket_id)

    def get_for_update(self, session: Session, ticket_id: str) -> Ticket | None:
        return session.exec(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        ).first()

    def get_by_conversation(
        self, session: Session, conversation_id: str
    ) -> Ticket | None:
        return session.exec(
            select(Ticket).where(Ticket.conversation_id == conversation_id)
        ).first()

    def count_chatbot_tickets_since(
        self, session: Session, client_id: str, since: datetime
    ) -> int:
        return int(
            session.exec(
                select(func.count(Ticket.id)).where(
                    Ticket.client_id == client_id,
                    Ticket.source == TicketSource.CHATBOT,
                    Ticket.created_at >= since,
                )
            ).one()
        )

    def list_tickets(
        self,
        session: Session,
        *,
        client_id: str | None = None,
        status: TicketStatus | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        source: TicketSource | None = None,
        assigned_advisor_id: str | None = None,
        search: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[Ticket]:
        statement, _ = self._ticket_statement(
            client_id=client_id,
            status=status,
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
        return list(
            session.exec(
                statement.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            ).all()
        )

    def list_tickets_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        client_id: str | None = None,
        status: TicketStatus | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        source: TicketSource | None = None,
        assigned_advisor_id: str | None = None,
        search: str | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> tuple[int, list[Ticket]]:
        statement, joins_client = self._ticket_statement(
            client_id=client_id,
            status=status,
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
        count_statement = select(func.count(Ticket.id)).select_from(Ticket)
        if joins_client:
            count_statement = count_statement.join(User, User.id == Ticket.client_id)
        count_statement = count_statement.where(
            *self._ticket_conditions(
                client_id=client_id,
                status=status,
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
        )
        total = session.exec(count_statement).one()
        items = list(
            session.exec(
                statement.order_by(Ticket.created_at.desc(), Ticket.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return total, items

    def list_operational_page(
        self,
        session: Session,
        *,
        queue: OperationalQueue,
        page: int,
        page_size: int,
        now: datetime,
        assigned_advisor_id: str | None = None,
        status: TicketStatus | None = None,
        category_id: str | None = None,
        priority: TicketPriority | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
        source: TicketSource | None = None,
        search: str | None = None,
        recent_hours: int = 24,
    ) -> tuple[int, list[Ticket]]:
        sla_queue = queue in {
            OperationalQueue.SLA_SOON,
            OperationalQueue.SLA_OVERDUE,
            OperationalQueue.PENDING_FIRST_RESPONSE,
        }
        statement = select(Ticket)
        if sla_queue:
            statement = statement.join(
                TicketSla, TicketSla.ticket_id == Ticket.id
            ).join(SlaPolicy, SlaPolicy.id == TicketSla.policy_id)
        if search:
            statement = statement.join(User, User.id == Ticket.client_id)
        conditions = self._ticket_conditions(
            client_id=None,
            status=status,
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
        conditions.append(
            Ticket.status.notin_((TicketStatus.CERRADO, TicketStatus.CANCELADO))
        )
        if sla_queue:
            conditions.append(
                TicketSla.status.notin_((SlaStatus.COMPLETED, SlaStatus.CANCELLED))
            )
        if queue == OperationalQueue.UNASSIGNED:
            conditions.append(Ticket.assigned_advisor_id.is_(None))
        elif queue in {
            OperationalQueue.ASSIGNED_TO_ME,
            OperationalQueue.ASSIGNED_TO_ADVISOR,
        }:
            conditions.append(Ticket.assigned_advisor_id == assigned_advisor_id)
        elif queue == OperationalQueue.SLA_SOON:
            max_warning = session.exec(
                select(func.max(SlaPolicy.warning_seconds)).where(SlaPolicy.is_active)
            ).one()
            window = now + timedelta(seconds=int(max_warning or 0))
            conditions.append(
                or_(
                    TicketSla.first_response_due_at.between(now, window),
                    TicketSla.resolution_due_at.between(now, window),
                )
            )
        elif queue == OperationalQueue.SLA_OVERDUE:
            conditions.append(
                or_(
                    (
                        TicketSla.first_responded_at.is_(None)
                        & (TicketSla.first_response_due_at < now)
                    ),
                    (
                        TicketSla.resolved_at.is_(None)
                        & (TicketSla.resolution_due_at < now)
                    ),
                )
            )
        elif queue == OperationalQueue.PENDING_FIRST_RESPONSE:
            conditions.append(TicketSla.first_responded_at.is_(None))
        elif queue == OperationalQueue.RECENTLY_UPDATED:
            conditions.append(Ticket.updated_at >= now - timedelta(hours=recent_hours))

        count_statement = select(func.count(Ticket.id)).select_from(Ticket)
        if sla_queue:
            count_statement = count_statement.join(
                TicketSla, TicketSla.ticket_id == Ticket.id
            ).join(SlaPolicy, SlaPolicy.id == TicketSla.policy_id)
        if search:
            count_statement = count_statement.join(User, User.id == Ticket.client_id)
        total = session.exec(count_statement.where(*conditions)).one()
        items = list(
            session.exec(
                statement.where(*conditions)
                .order_by(
                    Ticket.updated_at.desc(),
                    Ticket.created_at.desc(),
                    Ticket.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(total), items

    def _ticket_statement(self, **filters):
        statement = select(Ticket)
        conditions = self._ticket_conditions(**filters)
        if filters.get("search"):
            statement = statement.join(User, User.id == Ticket.client_id)
        return statement.where(*conditions), bool(filters.get("search"))

    @staticmethod
    def _ticket_conditions(
        *,
        client_id: str | None,
        status: TicketStatus | None,
        category_id: str | None,
        priority: TicketPriority | None,
        created_from: datetime | None,
        created_to: datetime | None,
        source: TicketSource | None,
        assigned_advisor_id: str | None,
        search: str | None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[object]:
        conditions: list[object] = []
        if client_id is not None:
            conditions.append(Ticket.client_id == client_id)
        if status is not None:
            conditions.append(Ticket.status == status)
        if category_id is not None:
            conditions.append(Ticket.category_id == category_id)
        if priority is not None:
            conditions.append(Ticket.priority == priority)
        if created_from is not None:
            conditions.append(Ticket.created_at >= created_from)
        if created_to is not None:
            conditions.append(Ticket.created_at <= created_to)
        if updated_from is not None:
            conditions.append(Ticket.updated_at >= updated_from)
        if updated_to is not None:
            conditions.append(Ticket.updated_at <= updated_to)
        if source is not None:
            conditions.append(Ticket.source == source)
        if assigned_advisor_id is not None:
            conditions.append(Ticket.assigned_advisor_id == assigned_advisor_id)
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(
                    Ticket.tracking_code.ilike(pattern),
                    Ticket.subject.ilike(pattern),
                    Ticket.description.ilike(pattern),
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )
        return conditions

    def add(self, session: Session, ticket: Ticket) -> Ticket:
        session.add(ticket)
        session.flush()
        return ticket

    def add_comment(self, session: Session, comment: TicketComment) -> TicketComment:
        session.add(comment)
        session.flush()
        return comment

    def add_assignment(
        self, session: Session, assignment: TicketAssignment
    ) -> TicketAssignment:
        session.add(assignment)
        session.flush()
        return assignment

    def current_assignment(
        self, session: Session, ticket_id: str
    ) -> TicketAssignment | None:
        return session.exec(
            select(TicketAssignment)
            .where(
                TicketAssignment.ticket_id == ticket_id,
                TicketAssignment.unassigned_at.is_(None),
            )
            .order_by(TicketAssignment.assigned_at.desc())
        ).first()

    @staticmethod
    def close_assignment(
        assignment: TicketAssignment, ended_at: datetime
    ) -> TicketAssignment:
        assignment.unassigned_at = ended_at
        return assignment

    def add_history(self, session: Session, history: TicketHistory) -> TicketHistory:
        session.add(history)
        session.flush()
        return history

    def comments(self, session: Session, ticket_id: str) -> list[TicketComment]:
        return list(
            session.exec(
                select(TicketComment)
                .where(TicketComment.ticket_id == ticket_id)
                .order_by(TicketComment.created_at)
            ).all()
        )

    def list_comments(self, session: Session, ticket_id: str) -> list[TicketComment]:
        return list(
            session.exec(
                select(TicketComment)
                .where(TicketComment.ticket_id == ticket_id)
                .order_by(TicketComment.created_at.asc())
            ).all()
        )

    def history(self, session: Session, ticket_id: str) -> list[TicketHistory]:
        return list(
            session.exec(
                select(TicketHistory)
                .where(TicketHistory.ticket_id == ticket_id)
                .order_by(TicketHistory.created_at)
            ).all()
        )

    def category_exists(self, session: Session, category_id: str) -> bool:
        category = session.get(TicketCategory, category_id)
        return category is not None and category.is_active

    def advisor_exists(self, session: Session, advisor_id: str) -> bool:
        from app.modules.usuarios.models.role import Role
        from app.modules.usuarios.models.user import User

        return (
            session.exec(
                select(User)
                .join(Role, User.role_id == Role.id)
                .where(User.id == advisor_id, User.is_active, Role.name == "ASESOR")
            ).first()
            is not None
        )

    def conversation(
        self, session: Session, conversation_id: str
    ) -> Conversation | None:
        return session.get(Conversation, conversation_id)

    def last_bot_message(
        self, session: Session, conversation_id: str
    ) -> ChatMessage | None:
        return session.exec(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.sender_type == SenderType.BOT,
            )
            .order_by(ChatMessage.created_at.desc())
        ).first()
