from sqlalchemy import or_
from sqlmodel import Session, select

from app.modules.tickets.models.sla import (
    SlaPolicy,
    SlaPolicyHistory,
    SlaStatus,
    TicketSla,
)
from app.modules.tickets.models.ticket import (
    Ticket,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.modules.usuarios.models.user import User


class SlaRepository:
    def add_policy(self, session: Session, policy: SlaPolicy) -> SlaPolicy:
        session.add(policy)
        session.flush()
        return policy

    def get_policy(self, session: Session, policy_id: str) -> SlaPolicy | None:
        return session.get(SlaPolicy, policy_id)

    def get_policy_by_key(self, session: Session, policy_key: str) -> SlaPolicy | None:
        return session.exec(
            select(SlaPolicy).where(SlaPolicy.policy_key == policy_key)
        ).first()

    def list_policies(self, session: Session) -> list[SlaPolicy]:
        return list(
            session.exec(
                select(SlaPolicy).order_by(
                    SlaPolicy.is_active.desc(), SlaPolicy.updated_at.desc()
                )
            ).all()
        )

    def add_policy_history(
        self, session: Session, history: SlaPolicyHistory
    ) -> SlaPolicyHistory:
        session.add(history)
        session.flush()
        return history

    def add_ticket_sla(self, session: Session, sla: TicketSla) -> TicketSla:
        session.add(sla)
        session.flush()
        return sla

    def get_ticket_sla(self, session: Session, ticket_id: str) -> TicketSla | None:
        return session.exec(
            select(TicketSla).where(TicketSla.ticket_id == ticket_id)
        ).first()

    def get_ticket_sla_by_id(self, session: Session, sla_id: str) -> TicketSla | None:
        return session.get(TicketSla, sla_id)

    def find_policy_for_ticket(
        self,
        session: Session,
        priority: TicketPriority,
        category_id: str,
        source: TicketSource,
    ) -> SlaPolicy | None:
        candidates = list(
            session.exec(
                select(SlaPolicy).where(
                    SlaPolicy.is_active.is_(True),
                    or_(SlaPolicy.priority == priority, SlaPolicy.priority.is_(None)),
                    or_(
                        SlaPolicy.category_id == category_id,
                        SlaPolicy.category_id.is_(None),
                    ),
                    or_(SlaPolicy.source == source, SlaPolicy.source.is_(None)),
                )
            ).all()
        )
        candidates.sort(
            key=lambda item: (
                item.priority is not None,
                item.category_id is not None,
                item.source is not None,
            ),
            reverse=True,
        )
        return candidates[0] if candidates else None

    def list_evaluable(
        self, session: Session
    ) -> list[tuple[TicketSla, Ticket, str | None, SlaPolicy]]:
        rows = session.exec(
            select(TicketSla, Ticket, User.id, SlaPolicy)
            .join(Ticket, Ticket.id == TicketSla.ticket_id)
            .join(SlaPolicy, SlaPolicy.id == TicketSla.policy_id)
            .join(User, User.id == Ticket.assigned_advisor_id, isouter=True)
            .where(
                Ticket.status.notin_((TicketStatus.CERRADO, TicketStatus.CANCELADO)),
                TicketSla.status.notin_((SlaStatus.COMPLETED, SlaStatus.CANCELLED)),
            )
        ).all()
        return [
            (sla, ticket, advisor_id, policy)
            for sla, ticket, advisor_id, policy in rows
        ]
