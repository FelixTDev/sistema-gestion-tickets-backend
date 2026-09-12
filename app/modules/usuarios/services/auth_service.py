from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlmodel import Session

from app.core.config import get_settings
from app.modules.usuarios.models.user import User
from app.modules.usuarios.repositories.user_repository import UserRepository
from app.modules.usuarios.schemas.auth import RegisterRequest

password_hasher = PasswordHash.recommended()


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def register(self, session: Session, data: RegisterRequest) -> User:
        email = str(data.email).lower()
        if self.repository.get_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError
        role = self.repository.get_role_by_name(session, "CLIENTE")
        if role is None:
            raise RuntimeError("El rol CLIENTE no está configurado")
        return self.repository.add(
            session,
            User(
                full_name=data.full_name.strip(),
                email=email,
                password_hash=password_hasher.hash(data.password),
                phone=data.phone,
                role_id=role.id,
            ),
        )

    def authenticate(self, session: Session, email: str, password: str) -> User:
        user = self.repository.get_by_email(session, email.lower())
        if (
            user is None
            or not user.is_active
            or not password_hasher.verify(password, user.password_hash)
        ):
            raise InvalidCredentialsError
        return user

    def role_for(self, session: Session, user: User) -> str:
        role = self.repository.get_role_by_id(session, user.role_id)
        if role is None:
            raise InvalidCredentialsError
        return role.name

    def create_access_token(self, user: User, role: str) -> str:
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=get_settings().access_token_expire_minutes)
        payload = {
            "sub": user.id,
            "role": role,
            "type": "access",
            "iat": now,
            "exp": expires,
        }
        return jwt.encode(
            payload, get_settings().secret_key, algorithm=get_settings().jwt_algorithm
        )
