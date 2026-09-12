from fastapi import APIRouter

from app.modules.usuarios.api.auth_router import router as auth_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ticket-management-api"}
