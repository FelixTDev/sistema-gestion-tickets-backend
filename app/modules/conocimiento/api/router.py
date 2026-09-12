from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.conocimiento.schemas.category import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
)
from app.modules.conocimiento.schemas.faq import (
    ActiveStatusUpdate,
    FAQCreate,
    FAQRead,
    FAQUpdate,
)
from app.modules.conocimiento.services.category_service import CategoryService
from app.modules.conocimiento.services.faq_service import FAQService

router = APIRouter(prefix="/faqs", tags=["faqs"])


def get_faq_service() -> FAQService:
    return FAQService()


def get_category_service() -> CategoryService:
    return CategoryService()


@router.get("", response_model=list[FAQRead])
def list_faqs(
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> list[FAQRead]:
    return service.list_active(session)


@router.get("/{faq_id}", response_model=FAQRead)
def get_faq(
    faq_id: str,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.get_active(session, faq_id)


@router.post("", response_model=FAQRead, status_code=201)
def create_faq(
    data: FAQCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.create(session, data, current_user)


@router.patch("/{faq_id}", response_model=FAQRead)
def update_faq(
    faq_id: str,
    data: FAQUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.update(session, faq_id, data, current_user)


@router.patch("/{faq_id}/status", response_model=FAQRead)
def set_faq_status(
    faq_id: str,
    data: ActiveStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.set_status(session, faq_id, data.is_active, current_user)


category_router = APIRouter(prefix="/categories", tags=["categories"])


@category_router.get("", response_model=list[CategoryRead])
def list_categories(
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> list[CategoryRead]:
    return service.list_active(session)


@category_router.post("", response_model=CategoryRead, status_code=201)
def create_category(
    data: CategoryCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> CategoryRead:
    return service.create(session, data, current_user)


@category_router.patch("/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: str,
    data: CategoryUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> CategoryRead:
    return service.update(session, category_id, data, current_user)


@category_router.patch("/{category_id}/status", response_model=CategoryRead)
def set_category_status(
    category_id: str,
    data: ActiveStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> CategoryRead:
    return service.set_status(session, category_id, data.is_active, current_user)
