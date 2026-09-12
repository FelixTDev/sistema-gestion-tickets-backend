from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.repositories.category_repository import CategoryRepository
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.conocimiento.schemas.faq import FAQCreate, FAQUpdate


class FAQService:
    def __init__(
        self,
        repository: FAQRepository | None = None,
        category_repository: CategoryRepository | None = None,
    ) -> None:
        self.repository = repository or FAQRepository()
        self.categories = category_repository or CategoryRepository()

    def list_active(self, session: Session) -> list[FAQ]:
        return self.repository.list_active(session)

    def get_active(self, session: Session, faq_id: str) -> FAQ:
        faq = self.repository.get_active_by_id(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        return faq

    def create(
        self, session: Session, data: FAQCreate, actor: AuthenticatedUser
    ) -> FAQ:
        self._require_supervisor(actor)
        self._ensure_category(session, data.category_id)
        faq = FAQ(
            category_id=data.category_id,
            question=data.question.strip(),
            answer=data.answer.strip(),
            keywords=data.keywords.strip(),
            created_by=actor.user.id,
        )
        self.repository.add(session, faq)
        session.commit()
        session.refresh(faq)
        return faq

    def update(
        self, session: Session, faq_id: str, data: FAQUpdate, actor: AuthenticatedUser
    ) -> FAQ:
        self._require_supervisor(actor)
        faq = self.repository.get(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        values = data.model_dump(exclude_unset=True)
        if "category_id" in values:
            self._ensure_category(session, values["category_id"])
        for field, value in values.items():
            setattr(faq, field, value.strip() if isinstance(value, str) else value)
        faq.updated_at = datetime.now(UTC)
        session.add(faq)
        session.commit()
        session.refresh(faq)
        return faq

    def set_status(
        self, session: Session, faq_id: str, is_active: bool, actor: AuthenticatedUser
    ) -> FAQ:
        self._require_supervisor(actor)
        faq = self.repository.get(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        faq.is_active = is_active
        faq.updated_at = datetime.now(UTC)
        session.add(faq)
        session.commit()
        session.refresh(faq)
        return faq

    def _ensure_category(self, session: Session, category_id: str) -> TicketCategory:
        category = self.categories.get(session, category_id)
        if category is None or not category.is_active:
            raise HTTPException(
                status_code=422, detail="La categoría no existe o está inactiva"
            )
        return category

    @staticmethod
    def _require_supervisor(actor: AuthenticatedUser) -> None:
        if actor.role != "SUPERVISOR":
            raise HTTPException(
                status_code=403,
                detail="Solo un supervisor puede realizar esta operación",
            )
