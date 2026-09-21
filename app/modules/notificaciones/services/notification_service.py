from datetime import UTC, datetime
from math import ceil

from fastapi import HTTPException
from sqlmodel import Session

from app.modules.auditoria.services.audit_service import AuditService
from app.modules.notificaciones.models.notification import (
    Notification,
    NotificationType,
)
from app.modules.notificaciones.repositories.notification_repository import (
    NotificationRepository,
)
from app.modules.notificaciones.schemas.notification import validate_metadata
from app.modules.notificaciones.services.provider import (
    NoopNotificationProvider,
    NotificationDelivery,
    NotificationProvider,
)
from app.modules.usuarios.models.user_preference import UserPreference
from app.shared.pagination import PaginationResult


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository | None = None,
        provider: NotificationProvider | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or NotificationRepository()
        self.provider = provider or NoopNotificationProvider()
        self.audit = audit_service or AuditService()

    def create(
        self,
        session: Session,
        *,
        recipient_user_id: str,
        notification_type: NotificationType | str,
        title: str,
        message: str,
        related_ticket_id: str | None = None,
        related_conversation_id: str | None = None,
        metadata: dict[str, object] | None = None,
        dedupe_key: str | None = None,
    ) -> Notification:
        existing = self.repository.find_by_dedupe_key(session, dedupe_key)
        if existing is not None:
            return existing
        safe_metadata = validate_metadata(metadata)
        notification = Notification(
            recipient_user_id=recipient_user_id,
            notification_type=NotificationType(notification_type),
            title=title.strip(),
            message=message.strip(),
            related_ticket_id=related_ticket_id,
            related_conversation_id=related_conversation_id,
            metadata_json=safe_metadata,
            dedupe_key=dedupe_key,
        )
        preference = session.get(UserPreference, recipient_user_id)
        in_app_enabled = self._allows_in_app(notification.notification_type, preference)
        email_enabled = self._allows_external_delivery(
            notification.notification_type, preference
        )
        if in_app_enabled:
            self.repository.add(session, notification)
        if notification.notification_type in {
            NotificationType.PASSWORD_CHANGED,
            NotificationType.SECURITY_EVENT,
            NotificationType.SLA_BREACHED,
        }:
            self.audit.record(
                session,
                event_type="NOTIFICATION",
                action="CRITICAL_CREATED",
                actor_user_id=None,
                actor_role=None,
                resource_type="NOTIFICATION",
                resource_id=notification.id,
                target_user_id=recipient_user_id,
                success=True,
                metadata={
                    "notification_type": notification.notification_type.value,
                    "related_ticket_id": related_ticket_id,
                },
                dedupe_key=f"audit:notification:{dedupe_key or notification.id}",
            )
        delivery = NotificationDelivery(
            notification_type=notification.notification_type,
            recipient_user_id=notification.recipient_user_id,
            title=notification.title,
            message=notification.message,
            metadata=safe_metadata,
        )
        if email_enabled:
            try:
                self.provider.publish(delivery)
            except Exception:
                # External delivery is best effort; the in-app record remains atomic.
                return notification
        return notification

    @staticmethod
    def _allows_in_app(
        notification_type: NotificationType, preference: UserPreference | None
    ) -> bool:
        if notification_type in {
            NotificationType.PASSWORD_CHANGED,
            NotificationType.SECURITY_EVENT,
        }:
            return True
        if preference is None or preference.in_app_enabled:
            if notification_type == NotificationType.TICKET_ASSIGNED:
                return preference is None or preference.assignment_enabled
            if notification_type == NotificationType.TICKET_STATUS_CHANGED:
                return preference is None or preference.status_change_enabled
            if notification_type == NotificationType.TICKET_COMMENTED:
                return preference is None or preference.comment_enabled
            if notification_type in {
                NotificationType.SLA_WARNING,
                NotificationType.SLA_BREACHED,
            }:
                return preference is None or preference.sla_enabled
            return True
        return False

    @staticmethod
    def _allows_external_delivery(
        notification_type: NotificationType, preference: UserPreference | None
    ) -> bool:
        if notification_type in {
            NotificationType.PASSWORD_CHANGED,
            NotificationType.SECURITY_EVENT,
        }:
            return True
        return preference is None or preference.email_enabled

    def list_for_user(
        self, session: Session, recipient_user_id: str, page: int, page_size: int
    ) -> PaginationResult[Notification]:
        total, items = self.repository.list_for_recipient(
            session, recipient_user_id, page, page_size
        )
        return PaginationResult(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
            items=items,
        )

    def unread_count(self, session: Session, recipient_user_id: str) -> int:
        return self.repository.count_unread(session, recipient_user_id)

    def mark_read(
        self, session: Session, notification_id: str, recipient_user_id: str
    ) -> Notification:
        notification = self.repository.get(session, notification_id)
        if notification is None:
            raise HTTPException(status_code=404, detail="Notificación no encontrada")
        if notification.recipient_user_id != recipient_user_id:
            raise HTTPException(status_code=403, detail="Notificación no autorizada")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.now(UTC)
            session.add(notification)
            session.commit()
            session.refresh(notification)
        return notification

    def mark_all_read(self, session: Session, recipient_user_id: str) -> int:
        updated_count = self.repository.mark_all_read(
            session, recipient_user_id, datetime.now(UTC)
        )
        session.commit()
        return updated_count
