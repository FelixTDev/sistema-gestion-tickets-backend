from sqlmodel import Session

from app.modules.usuarios.repositories.user_repository import UserRepository
from app.modules.usuarios.schemas.auth import PublicUser


class UserService:
    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository or UserRepository()

    def list_advisors(self, session: Session) -> list[PublicUser]:
        return [
            PublicUser(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                role=role.name,
            )
            for user, role in self.repository.list_active_advisors(session)
        ]
