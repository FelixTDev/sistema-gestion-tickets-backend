from fastapi import HTTPException
from sqlmodel import Session

from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.repositories.faq_repository import FAQRepository


class FAQService:
    def __init__(self, repository: FAQRepository | None = None) -> None:
        self.repository = repository or FAQRepository()

    def list_active(self, session: Session) -> list[FAQ]:
        return self.repository.list_active(session)

    def get_active(self, session: Session, faq_id: str) -> FAQ:
        faq = self.repository.get_active_by_id(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        return faq
