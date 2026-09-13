from sqlmodel import Session, select

from app.modules.usuarios.models.role import Role
from app.modules.usuarios.models.user import User


class UserRepository:
    def get_by_email(self, session: Session, email: str) -> User | None:
        return session.exec(select(User).where(User.email == email)).first()

    def get_by_id(self, session: Session, user_id: str) -> User | None:
        return session.get(User, user_id)

    def get_role_by_id(self, session: Session, role_id: str) -> Role | None:
        return session.get(Role, role_id)

    def get_role_by_name(self, session: Session, name: str) -> Role | None:
        return session.exec(select(Role).where(Role.name == name)).first()

    def list_active_advisors(self, session: Session) -> list[tuple[User, Role]]:
        statement = (
            select(User, Role)
            .join(Role, User.role_id == Role.id)
            .where(User.is_active, Role.name == "ASESOR")
            .order_by(User.full_name.asc(), User.id.asc())
        )
        return list(session.exec(statement).all())

    def add(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
