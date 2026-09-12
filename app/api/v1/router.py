from fastapi import APIRouter

from app.modules.chatbot.api.router import router as chatbot_router
from app.modules.conocimiento.api.router import category_router
from app.modules.conocimiento.api.router import router as faq_router
from app.modules.reportes.api.router import router as reports_router
from app.modules.tickets.api.router import router as tickets_router
from app.modules.usuarios.api.auth_router import router as auth_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(faq_router)
router.include_router(category_router)
router.include_router(chatbot_router)
router.include_router(tickets_router)
router.include_router(reports_router)


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ticket-management-api"}
