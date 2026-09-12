from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db.session import get_session
from app.modules.conocimiento.schemas.faq import FAQRead
from app.modules.conocimiento.services.faq_service import FAQService

router = APIRouter(prefix="/faqs", tags=["faqs"])


def get_faq_service() -> FAQService:
    return FAQService()


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
