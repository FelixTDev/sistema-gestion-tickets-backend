import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.shared.exceptions import AppError

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_SERVER_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

HTTP_ERROR_MESSAGES = {
    400: "La solicitud no es válida.",
    401: "Autenticación requerida.",
    403: "No tienes permisos para realizar esta operación.",
    404: "El recurso solicitado no existe.",
    409: "La solicitud entra en conflicto con el estado actual.",
    413: "El contenido enviado excede el tamaño permitido.",
    415: "El tipo de contenido no está permitido.",
    422: "La solicitud contiene datos inválidos.",
    429: "Se alcanzó el límite de solicitudes.",
    500: "Ocurrió un error interno.",
    503: "El servicio no está disponible.",
}


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorResponse(BaseModel):
    code: str = Field(description="Código estable y legible por clientes")
    message: str
    details: list[ErrorDetail] | dict[str, object] | None = None
    request_id: str


def request_id_for(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and REQUEST_ID_PATTERN.fullmatch(existing):
        return existing
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid4())
    request.state.request_id = request_id
    return request_id


def error_responses(*status_codes: int) -> dict[int, dict[str, object]]:
    return {
        status_code: {
            "model": ErrorResponse,
            "description": HTTP_ERROR_MESSAGES.get(
                status_code, "La solicitud no pudo completarse."
            ),
        }
        for status_code in status_codes
    }


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_for(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    content = ErrorResponse(
        code=code,
        message=message,
        details=details,
        request_id=request_id,
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=response_headers,
    )


async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
    return _error_response(
        request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details,
        headers=error.headers,
    )


async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
    status_code = error.status_code
    code = HTTP_ERROR_CODES.get(status_code, f"HTTP_{status_code}")
    message = (
        error.detail
        if status_code < 500 and isinstance(error.detail, str)
        else HTTP_ERROR_MESSAGES.get(status_code, "La solicitud no pudo completarse.")
    )
    return _error_response(
        request,
        status_code=status_code,
        code=code,
        message=message,
        headers=dict(error.headers or {}),
    )


def _validation_code(request: Request, errors: list[dict[str, Any]]) -> tuple[str, str]:
    if request.url.path.endswith("/release"):
        for error in errors:
            location = error.get("loc", ())
            error_type = error.get("type")
            if (
                location == ("body",)
                or "reason" in location
                and error_type in {"missing", "string_too_short", "value_error"}
            ):
                return (
                    "TICKET_RELEASE_REASON_REQUIRED",
                    "El motivo de liberación es obligatorio.",
                )
    return HTTP_ERROR_CODES[422], HTTP_ERROR_MESSAGES[422]


async def validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    raw_errors = error.errors()
    code, message = _validation_code(request, raw_errors)
    details = [
        {
            "loc": [
                item if isinstance(item, (str, int)) else str(item)
                for item in validation_error.get("loc", ())
            ],
            "msg": str(validation_error.get("msg", "Dato inválido")),
            "type": str(validation_error.get("type", "value_error")),
        }
        for validation_error in raw_errors
    ]
    return _error_response(
        request,
        status_code=422,
        code=code,
        message=message,
        details=details,
    )


async def unhandled_error_handler(request: Request, _error: Exception) -> JSONResponse:
    request_id = request_id_for(request)
    logger.error("Unhandled application error request_id=%s", request_id)
    return _error_response(
        request,
        status_code=500,
        code=HTTP_ERROR_CODES[500],
        message=HTTP_ERROR_MESSAGES[500],
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        validation_error_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, unhandled_error_handler)
