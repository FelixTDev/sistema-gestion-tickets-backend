from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.shared.api_errors import (
    REQUEST_ID_HEADER,
    install_error_handlers,
    request_id_for,
    unhandled_error_handler,
)

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    docs_url="/docs" if settings.app_env.casefold() == "development" else None,
    redoc_url="/redoc" if settings.app_env.casefold() == "development" else None,
    openapi_url="/openapi.json"
    if settings.app_env.casefold() == "development"
    else None,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


@app.middleware("http")
async def handle_unexpected_errors(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id_for(request)
    try:
        return await call_next(request)
    except Exception as error:
        return await unhandled_error_handler(request, error)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request_id_for(request)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


install_error_handlers(app)
app.include_router(v1_router)
