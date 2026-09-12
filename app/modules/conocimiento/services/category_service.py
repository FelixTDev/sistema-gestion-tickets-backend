from datetime import UTC, datetime

from fastapi import HTTPException
from sqlmodel import Session, select

from app.api.deps import AuthenticatedUser
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.repositories.category_repository import CategoryRepository
from app.modules.conocimiento.schemas.category import CategoryCreate, CategoryUpdate
from app.modules.tickets.models.ticket import Ticket


class CategoryService:
    def __init__(self, repository: CategoryRepository | None = None) -> None:
        self.repository = repository or CategoryRepository()

    def list_active(self, session: Session) -> list[TicketCategory]:
        return self.repository.list_active(session)

    def create(
        self, session: Session, data: CategoryCreate, actor: AuthenticatedUser
    ) -> TicketCategory:
        self._require_supervisor(actor)
        name = data.name.strip()
        if self.repository.get_by_name(session, name) is not None:
            raise HTTPException(
                status_code=409, detail="El nombre de categoría ya existe"
            )
        category = TicketCategory(name=name, description=data.description.strip())
        self.repository.add(session, category)
        session.commit()
        session.refresh(category)
        return category

    def update(
        self,
        session: Session,
        category_id: str,
        data: CategoryUpdate,
        actor: AuthenticatedUser,
    ) -> TicketCategory:
        self._require_supervisor(actor)
        category = self._get(session, category_id)
        values = data.model_dump(exclude_unset=True)
        if "name" in values:
            name = values["name"].strip()
            duplicate = self.repository.get_by_name(session, name)
            if duplicate is not None and duplicate.id != category.id:
                raise HTTPException(
                    status_code=409, detail="El nombre de categoría ya existe"
                )
            values["name"] = name
        for field, value in values.items():
            setattr(category, field, value.strip() if isinstance(value, str) else value)
        category.updated_at = datetime.now(UTC)
        session.add(category)
        session.commit()
        session.refresh(category)
        return category

    def set_status(
        self,
        session: Session,
        category_id: str,
        is_active: bool,
        actor: AuthenticatedUser,
    ) -> TicketCategory:
        self._require_supervisor(actor)
        category = self._get(session, category_id)
        if not is_active:
            used = session.exec(
                select(Ticket).where(Ticket.category_id == category_id)
            ).first()
            if used is not None:
                raise HTTPException(
                    status_code=409,
                    detail="No se puede desactivar una categoría usada por tickets",
                )
        category.is_active = is_active
        category.updated_at = datetime.now(UTC)
        session.add(category)
        session.commit()
        session.refresh(category)
        return category

    def _get(self, session: Session, category_id: str) -> TicketCategory:
        category = self.repository.get(session, category_id)
        if category is None:
            raise HTTPException(status_code=404, detail="Categoría no encontrada")
        return category

    @staticmethod
    def _require_supervisor(actor: AuthenticatedUser) -> None:
        if actor.role != "SUPERVISOR":
            raise HTTPException(
                status_code=403,
                detail="Solo un supervisor puede realizar esta operación",
            )
