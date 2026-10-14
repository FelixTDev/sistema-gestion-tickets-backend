from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlmodel import Session

from app.api.deps import CurrentUser, OptionalCurrentUser
from app.db.session import get_session
from app.modules.chatbot.schemas.chatbot import (
    BotMessageResponse,
    ChatbotFeedbackMetrics,
    ChatFeedbackCreate,
    ChatFeedbackRead,
    ConversationActionRequest,
    ConversationRead,
    LinkConversationResponse,
    MessageCreate,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.tickets.schemas.ticket import TicketCreate, TicketRead
from app.modules.tickets.services.ticket_service import TicketService
from app.shared.api_errors import ErrorResponse, error_responses

router = APIRouter(
    prefix="/chat",
    tags=["chatbot"],
    responses=error_responses(400, 401, 403, 404, 409, 422, 429),
)
OPTIONAL_AUTH_OPENAPI = {"security": [{}, {"HTTPBearer": []}]}


def get_ticket_service() -> TicketService:
    return TicketService()


TicketServiceDependency = Annotated[TicketService, Depends(get_ticket_service)]


def get_chatbot_service(ticket_service: TicketServiceDependency) -> ChatbotService:
    return ChatbotService(ticket_service=ticket_service)


ChatbotServiceDependency = Annotated[ChatbotService, Depends(get_chatbot_service)]


@router.get("/admin/feedback/metrics", response_model=ChatbotFeedbackMetrics)
def chatbot_feedback_metrics(
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    service: ChatbotServiceDependency,
) -> ChatbotFeedbackMetrics:
    return service.feedback_metrics(session, current_user)


@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
)
def create_conversation(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.get_conversation(
        session,
        service.create_conversation(
            session,
            current_user,
            anonymous_client_ip=request.client.host if request.client else None,
        ).id,
        current_user,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
)
def get_conversation(
    conversation_id: str,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.get_conversation(session, conversation_id, current_user)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=BotMessageResponse,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
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
    "/conversations/{conversation_id}/escalate",
    response_model=ConversationRead,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
)
def escalate_conversation(
    conversation_id: str,
    data: ConversationActionRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.escalate(session, conversation_id, data.reason, current_user)


@router.post(
    "/conversations/{conversation_id}/reset",
    response_model=ConversationRead,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
)
def reset_conversation(
    conversation_id: str,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ConversationRead:
    return service.reset(session, conversation_id, current_user)


@router.post(
    "/conversations/{conversation_id}/feedback",
    response_model=ChatFeedbackRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=OPTIONAL_AUTH_OPENAPI,
)
def feedback_conversation(
    conversation_id: str,
    data: ChatFeedbackCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: OptionalCurrentUser,
    service: ChatbotServiceDependency,
) -> ChatFeedbackRead:
    return service.feedback(session, conversation_id, data, current_user)


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
    summary="Confirma y convierte una conversación en ticket",
    description=(
        "La solicitud autenticada de un cliente propietario de la conversación, "
        "con los campos TicketCreate completos, confirma la creación. Si la "
        "conversación tiene un borrador, este debe estar listo y los datos deben "
        "coincidir con el resumen revisado. Sin borrador se conserva la conversión "
        "estructurada directa de la Fase 1. La confianza de la última respuesta "
        "del asistente no bloquea la conversión. La primera conversión responde "
        "201 Created. Un reintento con el mismo payload devuelve el ticket original "
        "con 200 OK y no repite efectos laterales. Un payload distinto después de "
        "la conversión responde 409 Conflict."
    ),
    responses={
        201: {
            "model": TicketRead,
            "description": (
                "Primera conversión: crea el ticket y registra sus efectos laterales "
                "una sola vez."
            ),
        },
        200: {
            "model": TicketRead,
            "description": (
                "Reintento idéntico: devuelve el ticket original sin crear otro ni "
                "repetir auditoría, historial, SLA o notificaciones."
            ),
        },
        409: {
            "model": ErrorResponse,
            "description": (
                "Conflicto si el payload difiere del ticket ya creado, o si la "
                "conversación convertida no tiene un ticket recuperable."
            ),
        },
    },
)
def convert_conversation_to_ticket(
    conversation_id: str,
    data: TicketCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: CurrentUser,
    ticket_service: TicketServiceDependency,
    response: Response,
) -> TicketRead:
    result = ticket_service.convert_conversation(
        session, conversation_id, data, current_user
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return result.ticket
