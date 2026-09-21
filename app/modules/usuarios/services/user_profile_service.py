from datetime import UTC, datetime

from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.usuarios.models.user_preference import UserPreference
from app.modules.usuarios.repositories.user_profile_repository import (
    UserProfileRepository,
)
from app.modules.usuarios.schemas.profile import (
    PreferencesRead,
    PreferencesUpdate,
    ProfileRead,
    ProfileUpdate,
)


class UserProfileService:
    def __init__(
        self,
        repository: UserProfileRepository | None = None,
        notification_service: NotificationService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or UserProfileRepository()
        self.notifications = notification_service or NotificationService()
        self.audit = audit_service or AuditService()

    def get_profile(
        self, session: Session, current_user: AuthenticatedUser
    ) -> ProfileRead:
        user = current_user.user
        return ProfileRead(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            phone=user.phone,
            role=current_user.role,
            email_verified=user.email_verified,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def update_profile(
        self,
        session: Session,
        current_user: AuthenticatedUser,
        data: ProfileUpdate,
    ) -> ProfileRead:
        user = current_user.user
        now = datetime.now(UTC)
        changed_fields: list[str] = []
        for field_name in ("full_name", "phone"):
            if field_name not in data.model_fields_set:
                continue
            new_value = getattr(data, field_name)
            old_value = getattr(user, field_name)
            if new_value == old_value:
                continue
            setattr(user, field_name, new_value)
            changed_fields.append(field_name)
            self.repository.add_audit(
                session,
                actor_id=user.id,
                affected_user_id=user.id,
                field_name=field_name,
                old_value_redacted=self._redact_value(old_value),
                new_value_redacted=self._redact_value(new_value),
                created_at=now,
            )

        if changed_fields:
            user.updated_at = now
            session.add(user)
            self._create_security_notification(
                session,
                user.id,
                "profile_updated",
                changed_fields,
                now,
            )
            self.audit.record(
                session,
                event_type="USER",
                action="PROFILE_UPDATED",
                actor_user_id=user.id,
                actor_role=current_user.role,
                resource_type="USER",
                resource_id=user.id,
                target_user_id=user.id,
                success=True,
                metadata={"changed_fields": changed_fields},
            )
        session.commit()
        session.refresh(user)
        return self.get_profile(session, current_user)

    def get_preferences(
        self, session: Session, current_user: AuthenticatedUser
    ) -> PreferencesRead:
        preference, created = self.repository.ensure_preferences(
            session, current_user.user.id
        )
        if created:
            session.commit()
            session.refresh(preference)
        return self._to_preferences_read(preference)

    def update_preferences(
        self,
        session: Session,
        current_user: AuthenticatedUser,
        data: PreferencesUpdate,
    ) -> PreferencesRead:
        preference, _ = self.repository.ensure_preferences(
            session, current_user.user.id
        )
        now = datetime.now(UTC)
        changed_fields: list[str] = []
        preference_fields = (
            "in_app_enabled",
            "email_enabled",
            "assignment_enabled",
            "status_change_enabled",
            "comment_enabled",
            "sla_enabled",
            "preferred_language",
            "timezone",
        )
        for field_name in preference_fields:
            if field_name not in data.model_fields_set:
                continue
            new_value = getattr(data, field_name)
            old_value = getattr(preference, field_name)
            if new_value == old_value:
                continue
            setattr(preference, field_name, new_value)
            changed_fields.append(field_name)
            self.repository.add_audit(
                session,
                actor_id=current_user.user.id,
                affected_user_id=current_user.user.id,
                field_name=f"preference.{field_name}",
                old_value_redacted=self._redact_value(old_value),
                new_value_redacted=self._redact_value(new_value),
                created_at=now,
            )

        if changed_fields:
            preference.updated_at = now
            session.add(preference)
            self._create_security_notification(
                session,
                current_user.user.id,
                "preferences_updated",
                changed_fields,
                now,
            )
            self.audit.record(
                session,
                event_type="USER",
                action="PREFERENCES_UPDATED",
                actor_user_id=current_user.user.id,
                actor_role=current_user.role,
                resource_type="USER_PREFERENCES",
                resource_id=current_user.user.id,
                target_user_id=current_user.user.id,
                success=True,
                metadata={"changed_fields": changed_fields},
            )
        session.commit()
        session.refresh(preference)
        return self._to_preferences_read(preference)

    def _create_security_notification(
        self,
        session: Session,
        user_id: str,
        event: str,
        changed_fields: list[str],
        occurred_at: datetime,
    ) -> None:
        self.notifications.create(
            session,
            recipient_user_id=user_id,
            notification_type=NotificationType.SECURITY_EVENT,
            title="Configuración de seguridad actualizada",
            message="Se actualizó la configuración de tu cuenta.",
            metadata={"event": event, "changed_fields": ",".join(changed_fields)},
            dedupe_key=f"security:{event}:{user_id}:{occurred_at.isoformat()}",
        )

    @staticmethod
    def _to_preferences_read(preference: UserPreference) -> PreferencesRead:
        return PreferencesRead(
            in_app_enabled=preference.in_app_enabled,
            email_enabled=preference.email_enabled,
            assignment_enabled=preference.assignment_enabled,
            status_change_enabled=preference.status_change_enabled,
            comment_enabled=preference.comment_enabled,
            sla_enabled=preference.sla_enabled,
            preferred_language=preference.preferred_language,
            timezone=preference.timezone,
            security_events_enabled=True,
        )

    @staticmethod
    def _redact_value(value: object | None) -> str | None:
        return None if value is None else "[REDACTED]"
