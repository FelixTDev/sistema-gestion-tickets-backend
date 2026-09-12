from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.deps import CurrentUser, OptionalCurrentUser
from app.db.session import get_session
from app.modules.chatbot.schemas.chatbot import (
    BotMessageResponse,
    ConversationRead,
    LinkConversationResponse,
    MessageCreate,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.tickets.schemas.ticket import TicketCreate, TicketRead
from app.modules.tickets.services.ticket_service import TicketService

router = APIRouter(prefix="/chat", tags=["chatbot"])


def get_chatbot_service() -> ChatbotService:
    return ChatbotService()


def get_ticket_service() -> TicketService:
    return TicketService()


ChatbotServiceDependency = Annotated[ChatbotService, Depends(get_chatbot_service)]
TicketServiceDependency = Annotated[TicketService, Depends(get_ticket_service)]


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.get_conversation(
        session,
        service.create_conversation(session, current_user).id,
        current_user,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: str,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.get_conversation(session, conversation_id, current_user)


@router.post(
    "/conversations/{conversation_id}/messages", response_model=BotMessageResponse
)
def send_message(
    conversation_id: str,
    data: MessageCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> BotMessageResponse:
    return service.send_message(session, conversation_id, data.content, current_user)


@router.post(
    "/conversations/{conversation_id}/link-user",
    response_model=LinkConversationResponse,
)
def link_conversation(
    conversation_id: str,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: ChatbotServiceDependency,
) -> LinkConversationResponse:
    return service.link_user(session, conversation_id, current_user)


@router.post(
    "/conversations/{conversation_id}/convert-to-ticket",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
)
def convert_conversation_to_ticket(
    conversation_id: str,
    data: TicketCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    ticket_service: TicketServiceDependency,
) -> TicketRead:
    return ticket_service.convert_conversation(
        session, conversation_id, data, current_user
    )
