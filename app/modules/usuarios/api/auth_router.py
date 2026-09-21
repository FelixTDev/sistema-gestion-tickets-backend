from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.usuarios.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MessageResponse,
    PublicUser,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from app.modules.usuarios.services.auth_service import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidCurrentPasswordError,
    InvalidOneTimeTokenError,
)
from app.modules.usuarios.services.email_provider import (
    EmailProvider,
    NoopEmailProvider,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def get_email_provider() -> EmailProvider:
    return NoopEmailProvider()


EmailProviderDependency = Annotated[EmailProvider, Depends(get_email_provider)]


def get_auth_service(email_provider: EmailProviderDependency) -> AuthService:
    return AuthService(email_provider=email_provider)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
SessionDependency = Annotated[Session, Depends(get_session)]


def _rate_limit_key(request: Request, value: str) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"{value.casefold()}|{client_host}"


def _client_rate_limit_key(request: Request) -> str:
    client_host = request.client.host if request.client is not None else "unknown"
    return f"client|{client_host}"


def public_user(service: AuthService, session: Session, user) -> PublicUser:
    return PublicUser(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=service.role_for(session, user),
    )


@router.post(
    "/register", response_model=PublicUser, status_code=status.HTTP_201_CREATED
)
def register(
    data: RegisterRequest,
    session: SessionDependency,
    service: AuthServiceDependency,
) -> PublicUser:
    try:
        user = service.register(session, data)
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El correo electrónico ya está registrado",
        ) from error
    return public_user(service, session, user)


@router.post("/login", response_model=LoginResponse)
def login(
    data: LoginRequest,
    request: Request,
    session: SessionDependency,
    service: AuthServiceDependency,
) -> LoginResponse:
    try:
        user = service.authenticate(
            session,
            str(data.email),
            data.password,
            _rate_limit_key(request, str(data.email)),
        )
        role = service.role_for(session, user)
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    token = service.create_access_token(session, user, role)
    return LoginResponse(
        access_token=token,
        user=PublicUser(
            id=user.id, full_name=user.full_name, email=user.email, role=role
        ),
    )


@router.get("/me", response_model=PublicUser)
def me(
    current_user: CurrentUser,
) -> PublicUser:
    return PublicUser(
        id=current_user.user.id,
        full_name=current_user.user.full_name,
        email=current_user.user.email,
        role=current_user.role,
    )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    session: SessionDependency,
    current_user: CurrentUser,
    service: AuthServiceDependency,
) -> LogoutResponse:
    service.revoke_session(session, current_user)
    return LogoutResponse(message="Sesión cerrada correctamente")


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    data: ForgotPasswordRequest,
    request: Request,
    session: SessionDependency,
    service: AuthServiceDependency,
) -> MessageResponse:
    service.request_password_reset(
        session, str(data.email), _rate_limit_key(request, str(data.email))
    )
    return MessageResponse(
        message=(
            "Si el correo está registrado, recibirás instrucciones para continuar."
        )
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    data: ResetPasswordRequest,
    request: Request,
    session: SessionDependency,
    service: AuthServiceDependency,
) -> MessageResponse:
    try:
        service.reset_password(session, data, _client_rate_limit_key(request))
    except InvalidOneTimeTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La solicitud no es válida o ya no está disponible",
        ) from error
    return MessageResponse(message="Contraseña actualizada correctamente")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    data: ChangePasswordRequest,
    session: SessionDependency,
    current_user: CurrentUser,
    service: AuthServiceDependency,
) -> MessageResponse:
    try:
        service.change_password(session, current_user, data)
    except InvalidCurrentPasswordError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo actualizar la contraseña",
        ) from error
    return MessageResponse(message="Contraseña actualizada correctamente")


@router.post("/verify-email", response_model=MessageResponse)
def verify_email(
    data: VerifyEmailRequest,
    request: Request,
    session: SessionDependency,
    service: AuthServiceDependency,
) -> MessageResponse:
    try:
        service.verify_email(session, data.token, _client_rate_limit_key(request))
    except InvalidOneTimeTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La solicitud no es válida o ya no está disponible",
        ) from error
    return MessageResponse(message="Correo electrónico verificado correctamente")
