from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import AuthenticatedUser, require_roles
from app.db.session import get_session
from app.modules.usuarios.schemas.auth import PublicUser
from app.modules.usuarios.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service() -> UserService:
    return UserService()


SessionDependency = Annotated[Session, Depends(get_session)]
UserServiceDependency = Annotated[UserService, Depends(get_user_service)]
SupervisorUser = Annotated[AuthenticatedUser, Depends(require_roles("SUPERVISOR"))]


@router.get("/advisors", response_model=list[PublicUser])
def list_advisors(
    session: SessionDependency,
    _: SupervisorUser,
    service: UserServiceDependency,
) -> list[PublicUser]:
    return service.list_advisors(session)
