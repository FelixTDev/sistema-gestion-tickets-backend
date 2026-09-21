from fastapi import APIRouter

from app.modules.adjuntos.api.router import router as attachments_router
from app.modules.auditoria.api.router import router as audit_router
from app.modules.chatbot.api.router import router as chatbot_router
from app.modules.conocimiento.api.router import category_router
from app.modules.conocimiento.api.router import router as faq_router
from app.modules.notificaciones.api.router import router as notifications_router
from app.modules.reportes.api.router import router as reports_router
from app.modules.tickets.api.router import router as tickets_router
from app.modules.tickets.api.sla_router import router as sla_router
from app.modules.usuarios.api.auth_router import router as auth_router
from app.modules.usuarios.api.user_router import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(faq_router)
router.include_router(category_router)
router.include_router(chatbot_router)
router.include_router(tickets_router)
router.include_router(sla_router)
router.include_router(attachments_router)
router.include_router(notifications_router)
router.include_router(reports_router)
router.include_router(audit_router)


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ticket-management-api"}
