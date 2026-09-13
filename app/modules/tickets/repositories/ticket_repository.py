from datetime import datetime

from sqlmodel import Session, select

from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.tickets.models.assignment import TicketAssignment
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket, TicketPriority, TicketStatus


class TicketRepository:
    def get(self, session: Session, ticket_id: str) -> Ticket | None:
        return session.get(Ticket, ticket_id)

    def get_by_conversation(
        self, session: Session, conversation_id: str
    ) -> Ticket | None:
        return session.exec(
            select(Ticket).where(Ticket.conversation_id == conversation_id)
        ).first()

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
    ) -> list[Ticket]:
        statement = select(Ticket)
        if client_id is not None:
            statement = statement.where(Ticket.client_id == client_id)
        if status is not None:
            statement = statement.where(Ticket.status == status)
        if category_id is not None:
            statement = statement.where(Ticket.category_id == category_id)
        if priority is not None:
            statement = statement.where(Ticket.priority == priority)
        if created_from is not None:
            statement = statement.where(Ticket.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(Ticket.created_at <= created_to)
        return list(session.exec(statement.order_by(Ticket.created_at.desc())).all())

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
