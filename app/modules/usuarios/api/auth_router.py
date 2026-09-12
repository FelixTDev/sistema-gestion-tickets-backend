from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.usuarios.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    PublicUser,
    RegisterRequest,
)
from app.modules.usuarios.services.auth_service import (
    AuthService,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service() -> AuthService:
    return AuthService()


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


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
    session: Annotated[Session, Depends(get_session)],
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
    session: Annotated[Session, Depends(get_session)],
    service: AuthServiceDependency,
) -> LoginResponse:
    try:
        user = service.authenticate(session, str(data.email), data.password)
        role = service.role_for(session, user)
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    token = service.create_access_token(user, role)
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
def logout(current_user: CurrentUser) -> LogoutResponse:
    return LogoutResponse(message="Sesión cerrada correctamente")
