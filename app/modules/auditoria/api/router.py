from datetime import UTC, datetime
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import AuthenticatedUser, require_roles
from app.db.session import get_session
from app.modules.auditoria.repositories.audit_repository import AuditRepository
from app.modules.auditoria.schemas.audit import AuditPage, AuditRead

router = APIRouter(prefix="/audit", tags=["audit"])


def get_audit_repository() -> AuditRepository:
    return AuditRepository()


Supervisor = Annotated[AuthenticatedUser, Depends(require_roles("SUPERVISOR"))]
SessionDependency = Annotated[Session, Depends(get_session)]
RepositoryDependency = Annotated[AuditRepository, Depends(get_audit_repository)]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.get("", response_model=AuditPage)
def list_audit(
    _: Supervisor,
    session: SessionDependency,
    repository: RepositoryDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    actor_user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    action: str | None = None,
    target_user_id: str | None = None,
    from_date: Annotated[datetime | None, Query(alias="from")] = None,
    to_date: Annotated[datetime | None, Query(alias="to")] = None,
    success: bool | None = None,
    search: str | None = None,
) -> AuditPage:
    from_utc, to_utc = _utc(from_date), _utc(to_date)
    if from_utc and to_utc and from_utc > to_utc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="El rango de fechas es inválido")
    filters = {
        "actor_user_id": actor_user_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "event_type": event_type,
        "action": action,
        "target_user_id": target_user_id,
        "from_date": from_utc,
        "to_date": to_utc,
        "success": success,
        "search": search,
    }
    total = repository.count(session, **filters)
    items = repository.list_page(session, page=page, page_size=page_size, **filters)
    return AuditPage(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=ceil(total / page_size) if total else 0,
        items=[AuditRead.from_model(item) for item in items],
    )
