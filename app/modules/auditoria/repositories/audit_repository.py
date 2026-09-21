from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.auditoria.models.audit_log import AuditLog


class AuditRepository:
    def add(self, session: Session, audit: AuditLog) -> AuditLog:
        session.add(audit)
        return audit

    def find_by_dedupe_key(self, session: Session, dedupe_key: str) -> AuditLog | None:
        return session.exec(
            select(AuditLog).where(AuditLog.dedupe_key == dedupe_key)
        ).first()

    def count(
        self,
        session: Session,
        *,
        actor_user_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        target_user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        success: bool | None = None,
        search: str | None = None,
    ) -> int:
        statement = self._statement(
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            event_type=event_type,
            action=action,
            target_user_id=target_user_id,
            from_date=from_date,
            to_date=to_date,
            success=success,
            search=search,
        )
        return int(
            session.exec(select(func.count()).select_from(statement.subquery())).one()
        )

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        actor_user_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        event_type: str | None = None,
        action: str | None = None,
        target_user_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        success: bool | None = None,
        search: str | None = None,
    ) -> list[AuditLog]:
        statement = self._statement(
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            event_type=event_type,
            action=action,
            target_user_id=target_user_id,
            from_date=from_date,
            to_date=to_date,
            success=success,
            search=search,
        ).order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        return list(
            session.exec(
                statement.offset((page - 1) * page_size).limit(page_size)
            ).all()
        )

    @staticmethod
    def _statement(
        *,
        actor_user_id: str | None,
        resource_type: str | None,
        resource_id: str | None,
        event_type: str | None,
        action: str | None,
        target_user_id: str | None,
        from_date: datetime | None,
        to_date: datetime | None,
        success: bool | None,
        search: str | None,
    ):
        statement = select(AuditLog)
        if actor_user_id:
            statement = statement.where(AuditLog.actor_user_id == actor_user_id)
        if resource_type:
            statement = statement.where(AuditLog.resource_type == resource_type)
        if resource_id:
            statement = statement.where(AuditLog.resource_id == resource_id)
        if event_type:
            statement = statement.where(AuditLog.event_type == event_type)
        if action:
            statement = statement.where(AuditLog.action == action)
        if target_user_id:
            statement = statement.where(AuditLog.target_user_id == target_user_id)
        if from_date:
            statement = statement.where(AuditLog.occurred_at >= from_date)
        if to_date:
            statement = statement.where(AuditLog.occurred_at <= to_date)
        if success is not None:
            statement = statement.where(AuditLog.success == success)
        if search:
            pattern = f"%{search}%"
            statement = statement.where(
                (AuditLog.event_type.ilike(pattern))
                | (AuditLog.action.ilike(pattern))
                | (AuditLog.resource_type.ilike(pattern))
                | (AuditLog.resource_id.ilike(pattern))
                | (AuditLog.request_id.ilike(pattern))
                | (AuditLog.correlation_id.ilike(pattern))
            )
        return statement
