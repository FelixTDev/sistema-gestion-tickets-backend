from datetime import datetime

from sqlmodel import Session

from app.modules.usuarios.models.user_preference import UserPreference
from app.modules.usuarios.models.user_profile_audit import UserProfileAudit


class UserProfileRepository:
    def get_preferences(self, session: Session, user_id: str) -> UserPreference | None:
        return session.get(UserPreference, user_id)

    def ensure_preferences(
        self, session: Session, user_id: str
    ) -> tuple[UserPreference, bool]:
        preference = self.get_preferences(session, user_id)
        if preference is not None:
            return preference, False
        preference = UserPreference(user_id=user_id)
        session.add(preference)
        session.flush()
        return preference, True

    def add_audit(
        self,
        session: Session,
        *,
        actor_id: str,
        affected_user_id: str,
        field_name: str,
        old_value_redacted: str | None,
        new_value_redacted: str | None,
        created_at: datetime,
    ) -> UserProfileAudit:
        audit = UserProfileAudit(
            actor_id=actor_id,
            affected_user_id=affected_user_id,
            field_name=field_name,
            old_value_redacted=old_value_redacted,
            new_value_redacted=new_value_redacted,
            created_at=created_at,
        )
        session.add(audit)
        session.flush()
        return audit
