from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.deps import CurrentUser
from app.db.session import get_session
from app.modules.tickets.schemas.sla import (
    SlaActionRequest,
    SlaEvaluationRead,
    SlaPolicyCreate,
    SlaPolicyRead,
    SlaPolicyUpdate,
    TicketSlaRead,
)
from app.modules.tickets.services.sla_service import SlaService

router = APIRouter(tags=["sla"])


def get_sla_service() -> SlaService:
    return SlaService()


SessionDependency = Annotated[Session, Depends(get_session)]
SlaServiceDependency = Annotated[SlaService, Depends(get_sla_service)]


@router.get("/tickets/{ticket_id}/sla", response_model=TicketSlaRead)
def get_ticket_sla(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> TicketSlaRead:
    sla, policy = service.get_for_ticket(session, ticket_id, current_user)
    return service.to_read(sla, policy)


@router.post("/tickets/{ticket_id}/sla/pause", response_model=TicketSlaRead)
def pause_ticket_sla(
    ticket_id: str,
    data: SlaActionRequest,
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> TicketSlaRead:
    sla, policy = service.pause(session, ticket_id, current_user, data.reason)
    return service.to_read(sla, policy)


@router.post("/tickets/{ticket_id}/sla/resume", response_model=TicketSlaRead)
def resume_ticket_sla(
    ticket_id: str,
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> TicketSlaRead:
    sla, policy = service.resume(session, ticket_id, current_user)
    return service.to_read(sla, policy)


@router.post("/sla/evaluate", response_model=SlaEvaluationRead)
def evaluate_sla(
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> SlaEvaluationRead:
    result = service.evaluate_as_supervisor(session, current_user)
    return SlaEvaluationRead(
        evaluated=result.evaluated,
        warnings=result.warnings,
        breaches=result.breaches,
    )


@router.get("/sla/policies", response_model=list[SlaPolicyRead])
def list_sla_policies(
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> list[SlaPolicyRead]:
    return [
        SlaPolicyRead.model_validate(policy)
        for policy in service.list_policies(session, current_user)
    ]


@router.post(
    "/sla/policies",
    response_model=SlaPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sla_policy(
    data: SlaPolicyCreate,
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> SlaPolicyRead:
    policy = service.create_policy(session, data, current_user)
    return SlaPolicyRead.model_validate(policy)


@router.patch("/sla/policies/{policy_id}", response_model=SlaPolicyRead)
def update_sla_policy(
    policy_id: str,
    data: SlaPolicyUpdate,
    session: SessionDependency,
    current_user: CurrentUser,
    service: SlaServiceDependency,
) -> SlaPolicyRead:
    policy = service.update_policy(session, policy_id, data, current_user)
    return SlaPolicyRead.model_validate(policy)
