from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlmodel import Session

from app.core.config import get_settings
from app.modules.usuarios.models.auth_rate_limit import AuthRateLimit
from app.modules.usuarios.repositories.auth_repository import AuthRepository
from app.shared.datetime import as_utc


class AuthRateLimiter:
    def __init__(self, repository: AuthRepository | None = None) -> None:
        self.repository = repository or AuthRepository()

    @staticmethod
    def fingerprint(action: str, key: str) -> str:
        return sha256(f"{action}:{key}".encode()).hexdigest()

    def allow(self, session: Session, action: str, key: str) -> bool:
        settings = get_settings()
        now = datetime.now(UTC)
        key_hash = self.fingerprint(action, key)
        record = self.repository.get_rate_limit(session, action, key_hash)
        if record is None:
            self.repository.add_rate_limit(
                session,
                AuthRateLimit(
                    action=action,
                    key_hash=key_hash,
                    window_started_at=now,
                    attempt_count=1,
                ),
            )
            session.commit()
            return True

        if record.blocked_until is not None and now < as_utc(record.blocked_until):
            return False

        window = timedelta(seconds=settings.auth_rate_limit_window_seconds)
        if now - as_utc(record.window_started_at) >= window:
            record.window_started_at = now
            record.attempt_count = 1
            record.blocked_until = None
            session.add(record)
            session.commit()
            return True

        if record.attempt_count >= settings.auth_rate_limit_max_attempts:
            record.blocked_until = now + timedelta(
                seconds=settings.auth_rate_limit_block_seconds
            )
            session.add(record)
            session.commit()
            return False

        record.attempt_count += 1
        session.add(record)
        session.commit()
        return True

    def reset(self, session: Session, action: str, key: str) -> None:
        key_hash = self.fingerprint(action, key)
        record = self.repository.get_rate_limit(session, action, key_hash)
        if record is not None:
            session.delete(record)
            session.commit()
