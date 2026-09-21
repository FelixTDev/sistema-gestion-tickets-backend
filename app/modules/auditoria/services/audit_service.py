from datetime import UTC, datetime

from sqlmodel import Session

from app.modules.auditoria.models.audit_log import AuditLog
from app.modules.auditoria.repositories.audit_repository import AuditRepository
from app.modules.auditoria.services.redaction import redact_data


class AuditService:
    def __init__(self, repository: AuditRepository | None = None) -> None:
        self.repository = repository or AuditRepository()

    def record(
        self,
        session: Session,
        *,
        event_type: str,
        action: str,
        actor_user_id: str | None,
        actor_role: str | None,
        resource_type: str,
        resource_id: str | None,
        target_user_id: str | None = None,
        occurred_at: datetime | None = None,
        success: bool,
        error_code: str | None = None,
        before_data: object | None = None,
        after_data: object | None = None,
        metadata: object | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        dedupe_key: str | None = None,
    ) -> AuditLog:
        if dedupe_key:
            existing = self.repository.find_by_dedupe_key(session, dedupe_key)
            if existing is not None:
                return existing
        audit = AuditLog(
            event_type=event_type,
            action=action,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            resource_type=resource_type,
            resource_id=resource_id,
            target_user_id=target_user_id,
            occurred_at=self._utc(occurred_at),
            success=success,
            error_code=error_code,
            before_data=redact_data(before_data) if before_data is not None else None,
            after_data=redact_data(after_data) if after_data is not None else None,
            metadata_json=redact_data(metadata) if metadata is not None else None,
            request_id=request_id,
            correlation_id=correlation_id,
            ip_address=ip_address,
            user_agent=redact_data(user_agent) if user_agent is not None else None,
            dedupe_key=dedupe_key,
        )
        return self.repository.add(session, audit)

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        timestamp = value or datetime.now(UTC)
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)
