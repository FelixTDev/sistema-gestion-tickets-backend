from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core.config import get_settings
from app.db.session import get_session
from app.modules.usuarios.models.user import User
from app.modules.usuarios.repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    user: User
    role: str


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> AuthenticatedUser:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de autenticación inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    current_user = _authenticate(credentials, session, unauthorized)
    return current_user


def get_optional_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> AuthenticatedUser | None:
    if credentials is None:
        return None
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de autenticación inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    return _authenticate(credentials, session, unauthorized)


def _authenticate(
    credentials: HTTPAuthorizationCredentials | None,
    session: Session,
    unauthorized: HTTPException,
) -> AuthenticatedUser:
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().secret_key,
            algorithms=[get_settings().jwt_algorithm],
        )
        user_id = payload.get("sub")
        if payload.get("type") != "access" or not isinstance(user_id, str):
            raise unauthorized
    except jwt.PyJWTError as error:
        raise unauthorized from error

    repository = UserRepository()
    user = repository.get_by_id(session, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    role = repository.get_role_by_id(session, user.role_id)
    if role is None:
        raise unauthorized
    return AuthenticatedUser(user=user, role=role.name)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
OptionalCurrentUser = Annotated[
    AuthenticatedUser | None, Depends(get_optional_current_user)
]


def require_roles(*allowed_roles: str):
    def role_dependency(current_user: CurrentUser) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta operación",
            )
        return current_user

    return role_dependency
