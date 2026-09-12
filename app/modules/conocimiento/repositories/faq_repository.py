from sqlmodel import Session, select

from app.modules.conocimiento.models.faq import FAQ


class FAQRepository:
    def list_active(self, session: Session) -> list[FAQ]:
        return list(
            session.exec(
                select(FAQ).where(FAQ.is_active).order_by(FAQ.created_at)
            ).all()
        )

    def get_active_by_id(self, session: Session, faq_id: str) -> FAQ | None:
        return session.exec(select(FAQ).where(FAQ.id == faq_id, FAQ.is_active)).first()

    def get(self, session: Session, faq_id: str) -> FAQ | None:
        return session.get(FAQ, faq_id)

    def add(self, session: Session, faq: FAQ) -> FAQ:
        session.add(faq)
        session.flush()
        return faq

    def list_all(self, session: Session) -> list[FAQ]:
        return list(session.exec(select(FAQ).order_by(FAQ.created_at)).all())
