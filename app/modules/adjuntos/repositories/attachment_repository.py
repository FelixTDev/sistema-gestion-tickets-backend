from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.adjuntos.models.attachment import Attachment, AttachmentStatus
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket


class AttachmentRepository:
    def get_ticket(self, session: Session, ticket_id: str) -> Ticket | None:
        return session.get(Ticket, ticket_id)

    def get_comment(self, session: Session, comment_id: str) -> TicketComment | None:
        return session.get(TicketComment, comment_id)

    def add(self, session: Session, attachment: Attachment) -> Attachment:
        session.add(attachment)
        session.flush()
        return attachment

    def add_history(self, session: Session, history: TicketHistory) -> TicketHistory:
        session.add(history)
        session.flush()
        return history

    def get(self, session: Session, attachment_id: str) -> Attachment | None:
        return session.get(Attachment, attachment_id)

    def list_active_for_ticket(
        self, session: Session, ticket_id: str
    ) -> list[Attachment]:
        return list(
            session.exec(
                select(Attachment)
                .where(
                    Attachment.ticket_id == ticket_id,
                    Attachment.status == AttachmentStatus.ACTIVE,
                )
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            ).all()
        )

    def count_active_for_ticket(self, session: Session, ticket_id: str) -> int:
        return int(
            session.exec(
                select(func.count(Attachment.id)).where(
                    Attachment.ticket_id == ticket_id,
                    Attachment.status == AttachmentStatus.ACTIVE,
                )
            ).one()
        )

    def total_size_active_for_ticket(self, session: Session, ticket_id: str) -> int:
        total = session.exec(
            select(func.coalesce(func.sum(Attachment.file_size), 0)).where(
                Attachment.ticket_id == ticket_id,
                Attachment.status == AttachmentStatus.ACTIVE,
            )
        ).one()
        return int(total or 0)

    def mark_deleted(self, attachment: Attachment, deleted_at: datetime) -> Attachment:
        attachment.status = AttachmentStatus.DELETED
        attachment.deleted_at = deleted_at
        return attachment
