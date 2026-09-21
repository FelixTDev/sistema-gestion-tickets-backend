from dataclasses import dataclass
from typing import Protocol

from app.modules.notificaciones.models.notification import NotificationType


@dataclass(frozen=True)
class NotificationDelivery:
    notification_type: NotificationType
    recipient_user_id: str
    title: str
    message: str
    metadata: dict[str, object]


class NotificationProvider(Protocol):
    def publish(self, delivery: NotificationDelivery) -> None:
        """Publish to a future channel without changing persistence semantics."""


class NoopNotificationProvider:
    """Explicit development provider: no network, logging, or external delivery."""

    def publish(self, delivery: NotificationDelivery) -> None:
        return None
