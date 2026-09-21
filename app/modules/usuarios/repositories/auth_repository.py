from datetime import datetime

from sqlmodel import Session, select

from app.modules.usuarios.models.auth_rate_limit import AuthRateLimit
from app.modules.usuarios.models.auth_session import AuthSession
from app.modules.usuarios.models.auth_token import AuthToken, AuthTokenPurpose


class AuthRepository:
    def add_token(self, session: Session, token: AuthToken) -> AuthToken:
        session.add(token)
        session.flush()
        return token

    def find_token(
        self,
        session: Session,
        purpose: AuthTokenPurpose,
        token_hash: str,
    ) -> AuthToken | None:
        return session.exec(
            select(AuthToken).where(
                AuthToken.purpose == purpose,
                AuthToken.token_hash == token_hash,
                AuthToken.used_at.is_(None),
            )
        ).first()

    def invalidate_tokens(
        self,
        session: Session,
        user_id: str,
        purpose: AuthTokenPurpose,
        now: datetime,
    ) -> None:
        tokens = session.exec(
            select(AuthToken).where(
                AuthToken.user_id == user_id,
                AuthToken.purpose == purpose,
                AuthToken.used_at.is_(None),
            )
        ).all()
        for token in tokens:
            token.used_at = now
            session.add(token)

    def add_session(self, session: Session, auth_session: AuthSession) -> AuthSession:
        session.add(auth_session)
        session.flush()
        return auth_session

    def get_session(self, session: Session, session_id: str) -> AuthSession | None:
        return session.get(AuthSession, session_id)

    def revoke_session(self, session: Session, session_id: str, now: datetime) -> None:
        auth_session = self.get_session(session, session_id)
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = now
            session.add(auth_session)

    def revoke_all_sessions(
        self, session: Session, user_id: str, now: datetime
    ) -> None:
        sessions = session.exec(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        ).all()
        for auth_session in sessions:
            auth_session.revoked_at = now
            session.add(auth_session)

    def get_rate_limit(
        self, session: Session, action: str, key_hash: str
    ) -> AuthRateLimit | None:
        return session.exec(
            select(AuthRateLimit).where(
                AuthRateLimit.action == action,
                AuthRateLimit.key_hash == key_hash,
            )
        ).first()

    def add_rate_limit(
        self, session: Session, rate_limit: AuthRateLimit
    ) -> AuthRateLimit:
        session.add(rate_limit)
        session.flush()
        return rate_limit
