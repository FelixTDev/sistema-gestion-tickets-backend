from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import jwt
from pwdlib import PasswordHash
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.usuarios.models.auth_session import AuthSession
from app.modules.usuarios.models.auth_token import AuthToken, AuthTokenPurpose
from app.modules.usuarios.models.user import User
from app.modules.usuarios.repositories.auth_repository import AuthRepository
from app.modules.usuarios.repositories.user_repository import UserRepository
from app.modules.usuarios.schemas.auth import (
    ChangePasswordRequest,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.modules.usuarios.services.email_provider import (
    EmailProvider,
    NoopEmailProvider,
)
from app.modules.usuarios.services.rate_limit_service import AuthRateLimiter
from app.shared.datetime import as_utc

password_hasher = PasswordHash.recommended()


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InvalidCurrentPasswordError(Exception):
    pass


class InvalidOneTimeTokenError(Exception):
    pass


class AuthService:
    def __init__(
        self,
        repository: UserRepository | None = None,
        auth_repository: AuthRepository | None = None,
        email_provider: EmailProvider | None = None,
        rate_limiter: AuthRateLimiter | None = None,
        notification_service: NotificationService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or UserRepository()
        self.auth_repository = auth_repository or AuthRepository()
        self.email_provider = email_provider or NoopEmailProvider()
        self.rate_limiter = rate_limiter or AuthRateLimiter(self.auth_repository)
        self.notifications = notification_service or NotificationService()
        self.audit = audit_service or AuditService()

    def register(self, session: Session, data: RegisterRequest) -> User:
        email = str(data.email).lower()
        if self.repository.get_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError
        role = self.repository.get_role_by_name(session, "CLIENTE")
        if role is None:
            raise RuntimeError("El rol CLIENTE no está configurado")
        user = self.repository.add(
            session,
            User(
                full_name=data.full_name.strip(),
                email=email,
                password_hash=password_hasher.hash(data.password),
                phone=data.phone,
                role_id=role.id,
                email_verified=False,
            ),
        )
        session.commit()
        session.refresh(user)
        self.issue_email_verification(session, user)
        return user

    def authenticate(
        self, session: Session, email: str, password: str, rate_limit_key: str
    ) -> User:
        if not self.rate_limiter.allow(session, "login", rate_limit_key):
            self.audit.record(
                session,
                event_type="AUTHENTICATION",
                action="LOGIN_FAILED",
                actor_user_id=None,
                actor_role=None,
                resource_type="AUTHENTICATION",
                resource_id=None,
                success=False,
                error_code="RATE_LIMITED",
                metadata={"rate_limited": True},
            )
            session.commit()
            raise InvalidCredentialsError
        user = self.repository.get_by_email(session, email.lower())
        if (
            user is None
            or not user.is_active
            or not password_hasher.verify(password, user.password_hash)
        ):
            self.audit.record(
                session,
                event_type="AUTHENTICATION",
                action="LOGIN_FAILED",
                actor_user_id=None,
                actor_role=None,
                resource_type="AUTHENTICATION",
                resource_id=None,
                success=False,
                error_code="INVALID_CREDENTIALS",
                metadata={"rate_limited": False},
            )
            session.commit()
            raise InvalidCredentialsError
        self.rate_limiter.reset(session, "login", rate_limit_key)
        return user

    def role_for(self, session: Session, user: User) -> str:
        role = self.repository.get_role_by_id(session, user.role_id)
        if role is None:
            raise InvalidCredentialsError
        return role.name

    def create_access_token(self, session: Session, user: User, role: str) -> str:
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=get_settings().access_token_expire_minutes)
        session_id = str(uuid4())
        self.auth_repository.add_session(
            session,
            AuthSession(
                id=session_id,
                user_id=user.id,
                issued_at=now,
                expires_at=expires,
            ),
        )
        self.audit.record(
            session,
            event_type="AUTHENTICATION",
            action="LOGIN_SUCCESS",
            actor_user_id=user.id,
            actor_role=role,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
            metadata={"session_issued": True},
        )
        session.commit()
        payload = {
            "sub": user.id,
            "role": role,
            "type": "access",
            "jti": session_id,
            "iat": now,
            "exp": expires,
        }
        return jwt.encode(
            payload, get_settings().secret_key, algorithm=get_settings().jwt_algorithm
        )

    def revoke_session(self, session: Session, current_user: AuthenticatedUser) -> None:
        if current_user.session_id is not None:
            self.auth_repository.revoke_session(
                session, current_user.session_id, datetime.now(UTC)
            )
            self.audit.record(
                session,
                event_type="AUTHENTICATION",
                action="LOGOUT",
                actor_user_id=current_user.user.id,
                actor_role=current_user.role,
                resource_type="SESSION",
                resource_id=current_user.session_id,
                target_user_id=current_user.user.id,
                success=True,
            )
            session.commit()

    def request_password_reset(
        self, session: Session, email: str, rate_limit_key: str
    ) -> None:
        allowed = self.rate_limiter.allow(session, "password_reset", rate_limit_key)
        if not allowed:
            return
        user = self.repository.get_by_email(session, email.lower())
        if user is None or not user.is_active:
            self.audit.record(
                session,
                event_type="AUTHENTICATION",
                action="PASSWORD_RESET_REQUESTED",
                actor_user_id=None,
                actor_role=None,
                resource_type="AUTHENTICATION",
                resource_id=None,
                success=True,
                metadata={"account_exists": False},
            )
            session.commit()
            return
        raw_token, expires_at = self._create_one_time_token(
            session, user, AuthTokenPurpose.PASSWORD_RESET
        )
        self.audit.record(
            session,
            event_type="AUTHENTICATION",
            action="PASSWORD_RESET_REQUESTED",
            actor_user_id=None,
            actor_role=None,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
            metadata={"delivery_attempted": True},
        )
        session.commit()
        self._send_password_reset(user.email, raw_token, expires_at)

    def reset_password(
        self, session: Session, data: ResetPasswordRequest, rate_limit_key: str
    ) -> None:
        if not self.rate_limiter.allow(
            session, "password_reset_attempt", rate_limit_key
        ):
            raise InvalidOneTimeTokenError
        if not data.token.strip():
            raise InvalidOneTimeTokenError
        token_hash = self._hash_token(data.token)
        token = self.auth_repository.find_token(
            session, AuthTokenPurpose.PASSWORD_RESET, token_hash
        )
        now = datetime.now(UTC)
        if token is None or as_utc(token.expires_at) <= now:
            raise InvalidOneTimeTokenError
        user = self.repository.get_by_id(session, token.user_id)
        if user is None or not user.is_active:
            raise InvalidOneTimeTokenError
        token.used_at = now
        self.auth_repository.invalidate_tokens(
            session, user.id, AuthTokenPurpose.PASSWORD_RESET, now
        )
        user.password_hash = password_hasher.hash(data.new_password)
        user.sessions_invalidated_at = now
        user.updated_at = now
        self.auth_repository.revoke_all_sessions(session, user.id, now)
        session.add(user)
        session.add(token)
        self.notifications.create(
            session,
            recipient_user_id=user.id,
            notification_type=NotificationType.PASSWORD_CHANGED,
            title="Contraseña actualizada",
            message="Tu contraseña fue actualizada mediante recuperación de acceso.",
            metadata={"source": "password_reset"},
            dedupe_key=f"password_changed:reset:{token.id}",
        )
        self.audit.record(
            session,
            event_type="AUTHENTICATION",
            action="PASSWORD_RESET_COMPLETED",
            actor_user_id=user.id,
            actor_role=None,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
            metadata={"sessions_invalidated": True},
        )
        self.audit.record(
            session,
            event_type="SECURITY",
            action="SESSIONS_INVALIDATED",
            actor_user_id=user.id,
            actor_role=None,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
            metadata={"source": "password_reset"},
        )
        self.audit.record(
            session,
            event_type="SECURITY",
            action="PASSWORD_CHANGED",
            actor_user_id=user.id,
            actor_role=None,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
            metadata={"source": "password_reset"},
        )
        session.commit()

    def change_password(
        self,
        session: Session,
        current_user: AuthenticatedUser,
        data: ChangePasswordRequest,
    ) -> None:
        if not password_hasher.verify(
            data.current_password, current_user.user.password_hash
        ):
            raise InvalidCurrentPasswordError
        if data.current_password == data.new_password:
            raise InvalidCurrentPasswordError
        now = datetime.now(UTC)
        current_user.user.password_hash = password_hasher.hash(data.new_password)
        current_user.user.sessions_invalidated_at = now
        current_user.user.updated_at = now
        self.auth_repository.invalidate_tokens(
            session, current_user.user.id, AuthTokenPurpose.PASSWORD_RESET, now
        )
        self.auth_repository.revoke_all_sessions(session, current_user.user.id, now)
        session.add(current_user.user)
        self.notifications.create(
            session,
            recipient_user_id=current_user.user.id,
            notification_type=NotificationType.PASSWORD_CHANGED,
            title="Contraseña actualizada",
            message="Tu contraseña fue actualizada correctamente.",
            metadata={"source": "authenticated_change"},
            dedupe_key=f"password_changed:authenticated:{current_user.user.id}:{now.isoformat()}",
        )
        self.audit.record(
            session,
            event_type="SECURITY",
            action="PASSWORD_CHANGED",
            actor_user_id=current_user.user.id,
            actor_role=current_user.role,
            resource_type="USER",
            resource_id=current_user.user.id,
            target_user_id=current_user.user.id,
            success=True,
            metadata={"source": "authenticated_change", "sessions_invalidated": True},
        )
        self.audit.record(
            session,
            event_type="SECURITY",
            action="SESSIONS_INVALIDATED",
            actor_user_id=current_user.user.id,
            actor_role=current_user.role,
            resource_type="USER",
            resource_id=current_user.user.id,
            target_user_id=current_user.user.id,
            success=True,
            metadata={"source": "authenticated_change"},
        )
        session.commit()

    def issue_email_verification(self, session: Session, user: User) -> None:
        raw_token, expires_at = self._create_one_time_token(
            session, user, AuthTokenPurpose.EMAIL_VERIFICATION
        )
        session.commit()
        try:
            self.email_provider.send_email_verification(
                user.email, raw_token, expires_at
            )
        except Exception:
            return None

    def verify_email(
        self, session: Session, token_value: str, rate_limit_key: str
    ) -> None:
        if not self.rate_limiter.allow(
            session, "email_verification_attempt", rate_limit_key
        ):
            raise InvalidOneTimeTokenError
        if not token_value.strip():
            raise InvalidOneTimeTokenError
        token = self.auth_repository.find_token(
            session,
            AuthTokenPurpose.EMAIL_VERIFICATION,
            self._hash_token(token_value),
        )
        now = datetime.now(UTC)
        if token is None or as_utc(token.expires_at) <= now:
            raise InvalidOneTimeTokenError
        user = self.repository.get_by_id(session, token.user_id)
        if user is None or not user.is_active:
            raise InvalidOneTimeTokenError
        token.used_at = now
        user.email_verified = True
        user.updated_at = now
        session.add(token)
        session.add(user)
        self.audit.record(
            session,
            event_type="AUTHENTICATION",
            action="EMAIL_VERIFIED",
            actor_user_id=user.id,
            actor_role=None,
            resource_type="USER",
            resource_id=user.id,
            target_user_id=user.id,
            success=True,
        )
        session.commit()

    def _create_one_time_token(
        self, session: Session, user: User, purpose: AuthTokenPurpose
    ) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        self.auth_repository.invalidate_tokens(session, user.id, purpose, now)
        settings = get_settings()
        if purpose == AuthTokenPurpose.PASSWORD_RESET:
            expires_at = now + timedelta(
                minutes=settings.password_reset_token_expire_minutes
            )
        else:
            expires_at = now + timedelta(
                hours=settings.email_verification_token_expire_hours
            )
        raw_token = token_urlsafe(32)
        self.auth_repository.add_token(
            session,
            AuthToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=self._hash_token(raw_token),
                expires_at=expires_at,
            ),
        )
        return raw_token, expires_at

    def _send_password_reset(
        self, email: str, raw_token: str, expires_at: datetime
    ) -> None:
        try:
            self.email_provider.send_password_reset(email, raw_token, expires_at)
        except Exception:
            return None

    @staticmethod
    def _hash_token(token_value: str) -> str:
        return sha256(token_value.encode("utf-8")).hexdigest()
