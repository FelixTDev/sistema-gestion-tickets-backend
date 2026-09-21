from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import AuthenticatedUser, CurrentUser, require_roles
from app.db.session import get_session
from app.modules.usuarios.schemas.auth import PublicUser
from app.modules.usuarios.schemas.profile import (
    PreferencesRead,
    PreferencesUpdate,
    ProfileRead,
    ProfileUpdate,
)
from app.modules.usuarios.services.user_profile_service import UserProfileService
from app.modules.usuarios.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service() -> UserService:
    return UserService()


def get_user_profile_service() -> UserProfileService:
    return UserProfileService()


SessionDependency = Annotated[Session, Depends(get_session)]
UserServiceDependency = Annotated[UserService, Depends(get_user_service)]
UserProfileServiceDependency = Annotated[
    UserProfileService, Depends(get_user_profile_service)
]
SupervisorUser = Annotated[AuthenticatedUser, Depends(require_roles("SUPERVISOR"))]


@router.get("/advisors", response_model=list[PublicUser])
def list_advisors(
    session: SessionDependency,
    _: SupervisorUser,
    service: UserServiceDependency,
) -> list[PublicUser]:
    return service.list_advisors(session)


@router.get("/me/profile", response_model=ProfileRead)
def get_my_profile(
    current_user: CurrentUser,
    session: SessionDependency,
    service: UserProfileServiceDependency,
) -> ProfileRead:
    return service.get_profile(session, current_user)


@router.patch("/me/profile", response_model=ProfileRead)
def update_my_profile(
    data: ProfileUpdate,
    current_user: CurrentUser,
    session: SessionDependency,
    service: UserProfileServiceDependency,
) -> ProfileRead:
    return service.update_profile(session, current_user, data)


@router.get("/me/preferences", response_model=PreferencesRead)
def get_my_preferences(
    current_user: CurrentUser,
    session: SessionDependency,
    service: UserProfileServiceDependency,
) -> PreferencesRead:
    return service.get_preferences(session, current_user)


@router.patch("/me/preferences", response_model=PreferencesRead)
def update_my_preferences(
    data: PreferencesUpdate,
    current_user: CurrentUser,
    session: SessionDependency,
    service: UserProfileServiceDependency,
) -> PreferencesRead:
    return service.update_preferences(session, current_user, data)
