from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.api.deps import CurrentUser, OptionalCurrentUser
from app.db.session import get_session
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.schemas.category import (
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
)
from app.modules.conocimiento.schemas.faq import (
    ActiveStatusUpdate,
    FAQAdminPage,
    FAQCreate,
    FAQFeedbackCreate,
    FAQFeedbackRead,
    FAQPage,
    FAQRead,
    FAQStatus,
    FAQUpdate,
    FAQUtilityMetrics,
    FAQVersionRead,
    WorkflowStatusUpdate,
)
from app.modules.conocimiento.services.category_service import CategoryService
from app.modules.conocimiento.services.faq_service import FAQService
from app.shared.pagination import PaginationResult

router = APIRouter(prefix="/faqs", tags=["faqs"])


def get_faq_service() -> FAQService:
    return FAQService()


def get_category_service() -> CategoryService:
    return CategoryService()


@router.get("", response_model=list[FAQRead] | FAQPage)
def list_faqs(
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[FAQService, Depends(get_faq_service)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[str | None, Query(max_length=36)] = None,
    tag: Annotated[str | None, Query(max_length=80)] = None,
    published_from: Annotated[datetime | None, Query()] = None,
    published_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> list[FAQRead] | FAQPage:
    result = service.list_active(
        session,
        page=page,
        page_size=page_size,
        search=search,
        category_id=category_id,
        tag=tag,
        published_from=published_from,
        published_to=published_to,
    )
    return _public_response(result)


@router.get("/admin", response_model=FAQAdminPage)
def list_admin_faqs(
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
    status_filter: Annotated[FAQStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[str | None, Query(max_length=36)] = None,
    tag: Annotated[str | None, Query(max_length=80)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    updated_from: Annotated[datetime | None, Query()] = None,
    updated_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FAQAdminPage:
    result = service.list_admin(
        session,
        current_user,
        page=page,
        page_size=page_size,
        status=status_filter,
        search=search,
        category_id=category_id,
        tag=tag,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
    )
    return FAQAdminPage(
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        total_pages=result.total_pages,
        items=result.items,
    )


@router.get("/admin/metrics/utility", response_model=FAQUtilityMetrics)
def faq_utility_metrics(
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQUtilityMetrics:
    return service.utility_metrics(session, current_user)


@router.get("/{faq_id}/history", response_model=list[FAQVersionRead])
def get_faq_history(
    faq_id: str,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> list[FAQVersionRead]:
    return service.history(session, faq_id, current_user)


@router.post(
    "/{faq_id}/feedback",
    response_model=FAQFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
def add_faq_feedback(
    faq_id: str,
    data: FAQFeedbackCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQFeedbackRead:
    return service.add_feedback(session, faq_id, data, current_user)


@router.get("/{faq_id}", response_model=FAQRead)
def get_faq(
    faq_id: str,
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.get_active(session, faq_id)


@router.post("", response_model=FAQRead, status_code=status.HTTP_201_CREATED)
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


@router.patch("/{faq_id}/workflow", response_model=FAQRead)
def update_faq_workflow(
    faq_id: str,
    data: WorkflowStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.workflow(session, faq_id, data, current_user)


@router.patch("/{faq_id}/status", response_model=FAQRead)
def set_faq_status(
    faq_id: str,
    data: ActiveStatusUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: Annotated[FAQService, Depends(get_faq_service)],
) -> FAQRead:
    return service.set_status(session, faq_id, data.is_active, current_user)


def _public_response(result: list[FAQ] | PaginationResult[FAQ]):
    if isinstance(result, PaginationResult):
        return FAQPage(
            page=result.page,
            page_size=result.page_size,
            total=result.total,
            total_pages=result.total_pages,
            items=result.items,
        )
    return result


category_router = APIRouter(prefix="/categories", tags=["categories"])


@category_router.get("", response_model=list[CategoryRead])
def list_categories(
    session: Annotated[Session, Depends(get_session)],
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> list[CategoryRead]:
    return service.list_active(session)


@category_router.post(
    "", response_model=CategoryRead, status_code=status.HTTP_201_CREATED
)
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
