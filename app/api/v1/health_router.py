from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.db.readiness import DatabaseNotReadyError, ensure_database_ready
from app.db.session import get_session
from app.shared.api_errors import error_responses
from app.shared.exceptions import AppError

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str


class ReadinessResponse(HealthResponse):
    database: str


@router.get("", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ticket-management-api")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses=error_responses(503),
)
def readiness(
    session: Annotated[Session, Depends(get_session)],
) -> ReadinessResponse:
    try:
        ensure_database_ready(session)
    except DatabaseNotReadyError as error:
        raise AppError(
            503,
            "DATABASE_NOT_READY",
            "La aplicación no está lista.",
        ) from error
    return ReadinessResponse(
        status="ready",
        service="ticket-management-api",
        database="ok",
    )
