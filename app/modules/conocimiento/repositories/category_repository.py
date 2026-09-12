from sqlmodel import Session, select

from app.modules.conocimiento.models.category import TicketCategory


class CategoryRepository:
    def get(self, session: Session, category_id: str) -> TicketCategory | None:
        return session.get(TicketCategory, category_id)

    def get_by_name(self, session: Session, name: str) -> TicketCategory | None:
        return session.exec(
            select(TicketCategory).where(TicketCategory.name == name)
        ).first()

    def list_active(self, session: Session) -> list[TicketCategory]:
        return list(
            session.exec(
                select(TicketCategory)
                .where(TicketCategory.is_active)
                .order_by(TicketCategory.name)
            ).all()
        )

    def add(self, session: Session, category: TicketCategory) -> TicketCategory:
        session.add(category)
        session.flush()
        return category
