from datetime import datetime

from sqlalchemy import func, update
from sqlmodel import Session, select

from app.modules.notificaciones.models.notification import Notification


class NotificationRepository:
    def add(self, session: Session, notification: Notification) -> Notification:
        session.add(notification)
        session.flush()
        return notification

    def find_by_dedupe_key(
        self, session: Session, dedupe_key: str | None
    ) -> Notification | None:
        if dedupe_key is None:
            return None
        return session.exec(
            select(Notification).where(Notification.dedupe_key == dedupe_key)
        ).first()

    def list_for_recipient(
        self, session: Session, recipient_user_id: str, page: int, page_size: int
    ) -> tuple[int, list[Notification]]:
        filters = Notification.recipient_user_id == recipient_user_id
        total = session.exec(select(func.count(Notification.id)).where(filters)).one()
        items = list(
            session.exec(
                select(Notification)
                .where(filters)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(total), items

    def count_unread(self, session: Session, recipient_user_id: str) -> int:
        return int(
            session.exec(
                select(func.count(Notification.id)).where(
                    Notification.recipient_user_id == recipient_user_id,
                    Notification.is_read.is_(False),
                )
            ).one()
        )

    def get(self, session: Session, notification_id: str) -> Notification | None:
        return session.get(Notification, notification_id)

    def mark_all_read(
        self, session: Session, recipient_user_id: str, read_at: datetime
    ) -> int:
        result = session.exec(
            update(Notification)
            .where(
                Notification.recipient_user_id == recipient_user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=read_at)
        )
        return int(result.rowcount or 0)
