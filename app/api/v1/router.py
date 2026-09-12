from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ticket-management-api"}
