from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.chatbot.models.conversation import ConversationStatus
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus


class ChatbotSuggestedAction(StrEnum):
    NONE = "NONE"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    CONVERT_TO_TICKET = "CONVERT_TO_TICKET"
    CONTACT_ADVISOR = "CONTACT_ADVISOR"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    LIST_MY_TICKETS = "LIST_MY_TICKETS"
    COUNT_MY_TICKETS = "COUNT_MY_TICKETS"
    TICKET_STATUS = "TICKET_STATUS"
    ADVISOR_RESPONSE_STATUS = "ADVISOR_RESPONSE_STATUS"
    LATEST_ADVISOR_RESPONSE = "LATEST_ADVISOR_RESPONSE"
    CLIENT_ACTION_REQUIRED = "CLIENT_ACTION_REQUIRED"
    CLARIFY_TICKET_CODE = "CLARIFY_TICKET_CODE"


class ChatbotTicketQueryType(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    TICKET_LIST = "TICKET_LIST"
    TICKET_COUNT = "TICKET_COUNT"
    TICKET_STATUS = "TICKET_STATUS"
    ADVISOR_RESPONSE_STATUS = "ADVISOR_RESPONSE_STATUS"
    ADVISOR_REPLY = "ADVISOR_REPLY"
    CLIENT_ACTION_REQUIRED = "CLIENT_ACTION_REQUIRED"
    LATEST_TICKET = "LATEST_TICKET"
    CLARIFY_TICKET_CODE = "CLARIFY_TICKET_CODE"


class ChatbotAuthenticationAction(StrEnum):
    LOGIN = "LOGIN"
    REGISTER = "REGISTER"


class ChatbotTicketCard(BaseModel):
    tracking_code: str = Field(description="Código de seguimiento del ticket.")
    subject: str
    status: TicketStatus
    status_label: str = Field(description="Etiqueta en español del estado.")
    priority: TicketPriority
    priority_label: str = Field(description="Etiqueta en español de la prioridad.")
    updated_at: datetime
    assigned_advisor_name: str | None = None


class ChatbotTicketPagination(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=5)
    total_pages: int = Field(ge=0)


class ChatbotAdvisorResponse(BaseModel):
    author_name: str
    created_at: datetime
    content: str | None = Field(
        default=None,
        description=(
            "Contenido visible de la respuesta más reciente del asesor. Se omite "
            "en consultas que solo preguntan si ya respondió."
        ),
    )


class ChatbotTicketQuery(BaseModel):
    query_type: ChatbotTicketQueryType
    authentication_required: bool = False
    authentication_actions: list[ChatbotAuthenticationAction] = Field(
        default_factory=list
    )
    found: bool | None = None
    tickets: list[ChatbotTicketCard] = Field(default_factory=list)
    ticket: ChatbotTicketCard | None = None
    total: int | None = Field(default=None, ge=0)
    pagination: ChatbotTicketPagination | None = None
    advisor_responded: bool | None = None
    latest_advisor_response: ChatbotAdvisorResponse | None = None


class ChatbotResponseSource(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    AI = "AI"
    FALLBACK = "FALLBACK"


class ChatMessageRead(BaseModel):
    id: str
    sender_type: str
    content: str
    intent: str | None
    confidence: float | None
    faq_id: str | None = None
    response_source: ChatbotResponseSource | None = None
    created_at: datetime


class ConversationRead(BaseModel):
    id: str
    user_id: str | None
    status: ConversationStatus
    started_at: datetime
    ended_at: datetime | None
    detected_intent: str | None = None
    last_faq_id: str | None = None
    category_id: str | None = None
    pending_question: str | None = None
    turn_count: int = 0
    last_confidence: float | None = None
    escalation_reason: str | None = None
    escalated_at: datetime | None = None
    converted_at: datetime | None = None
    last_activity_at: datetime | None = None
    messages: list[ChatMessageRead] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El mensaje no puede estar vacío")
        return value


class ChatbotSource(BaseModel):
    title: str
    category: str | None = None
    url: str | None = None
    verified_at: datetime | None = None


class BotMessageResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_message": {
                        "id": "00000000-0000-4000-8000-000000000001",
                        "sender_type": "USER",
                        "content": "¿Cuántos tickets tengo?",
                        "intent": None,
                        "confidence": None,
                        "faq_id": None,
                        "response_source": None,
                        "created_at": "2026-10-10T14:29:00Z",
                    },
                    "bot_message": {
                        "id": "00000000-0000-4000-8000-000000000002",
                        "sender_type": "BOT",
                        "content": (
                            "Inicia sesión o regístrate para consultar tus tickets."
                        ),
                        "intent": "AUTHENTICATION_REQUIRED",
                        "confidence": 1.0,
                        "faq_id": None,
                        "response_source": "DETERMINISTIC",
                        "created_at": "2026-10-10T14:29:01Z",
                    },
                    "resolved": False,
                    "offers_ticket": False,
                    "confidence": 1.0,
                    "faq_id": None,
                    "source_category": None,
                    "requires_clarification": False,
                    "clarification_options": [],
                    "suggested_action": "AUTHENTICATION_REQUIRED",
                    "ticket_query": {
                        "query_type": "AUTHENTICATION_REQUIRED",
                        "authentication_required": True,
                        "authentication_actions": ["LOGIN", "REGISTER"],
                        "found": None,
                        "tickets": [],
                        "ticket": None,
                        "total": None,
                        "pagination": None,
                        "advisor_responded": None,
                        "latest_advisor_response": None,
                    },
                    "fallback_reason": None,
                    "conversation_status": "ACTIVE",
                    "response_source": "DETERMINISTIC",
                    "sources": [],
                },
                {
                    "user_message": {
                        "id": "00000000-0000-4000-8000-000000000003",
                        "sender_type": "USER",
                        "content": "¿Qué me respondió?",
                        "intent": None,
                        "confidence": None,
                        "faq_id": None,
                        "response_source": None,
                        "created_at": "2026-10-10T14:29:00Z",
                    },
                    "bot_message": {
                        "id": "00000000-0000-4000-8000-000000000004",
                        "sender_type": "BOT",
                        "content": (
                            "La respuesta más reciente del asesor está disponible."
                        ),
                        "intent": "ADVISOR_REPLY",
                        "confidence": 1.0,
                        "faq_id": None,
                        "response_source": "DETERMINISTIC",
                        "created_at": "2026-10-10T14:30:00Z",
                    },
                    "resolved": True,
                    "offers_ticket": False,
                    "confidence": 1.0,
                    "faq_id": None,
                    "source_category": None,
                    "requires_clarification": False,
                    "clarification_options": [],
                    "suggested_action": "LATEST_ADVISOR_RESPONSE",
                    "ticket_query": {
                        "query_type": "ADVISOR_REPLY",
                        "authentication_required": False,
                        "authentication_actions": [],
                        "found": True,
                        "tickets": [],
                        "ticket": {
                            "tracking_code": "TCK-20261010-AB12CD34EF",
                            "subject": "Consulta sobre mi solicitud",
                            "status": "EN_PROCESO",
                            "status_label": "En proceso",
                            "priority": "MEDIA",
                            "priority_label": "Media",
                            "updated_at": "2026-10-10T14:30:00Z",
                            "assigned_advisor_name": "Asesor Demo",
                        },
                        "total": None,
                        "pagination": None,
                        "advisor_responded": True,
                        "latest_advisor_response": {
                            "author_name": "Asesor Demo",
                            "created_at": "2026-10-10T14:30:00Z",
                            "content": (
                                "Revisaremos tu solicitud y te informaremos "
                                "el resultado."
                            ),
                        },
                    },
                    "fallback_reason": None,
                    "conversation_status": "ACTIVE",
                    "response_source": "DETERMINISTIC",
                    "sources": [],
                },
            ]
        }
    )

    user_message: ChatMessageRead
    bot_message: ChatMessageRead
    resolved: bool
    offers_ticket: bool
    confidence: float = 0.0
    faq_id: str | None = None
    source_category: str | None = None
    requires_clarification: bool = False
    clarification_options: list[str] = Field(default_factory=list)
    suggested_action: ChatbotSuggestedAction = Field(
        default=ChatbotSuggestedAction.NONE,
        description=(
            "Acción tipada que la interfaz puede presentar o ejecutar; "
            "nunca confirma por sí sola una conversión a ticket. Las acciones de "
            "consulta usan ticket_query como payload tipado."
        ),
    )
    ticket_query: ChatbotTicketQuery | None = Field(
        default=None,
        description=(
            "Resultado acotado de una consulta autenticada de tickets. Se omite "
            "para respuestas que no consultan tickets. El tipo de consulta y "
            "suggested_action permiten al frontend actuar sin analizar el texto."
        ),
        examples=[
            {
                "query_type": "AUTHENTICATION_REQUIRED",
                "authentication_required": True,
                "authentication_actions": ["LOGIN", "REGISTER"],
                "found": None,
                "tickets": [],
                "ticket": None,
                "total": None,
                "pagination": None,
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
            {
                "query_type": "TICKET_LIST",
                "authentication_required": False,
                "authentication_actions": [],
                "found": True,
                "tickets": [
                    {
                        "tracking_code": "TCK-20261010-AB12CD34EF",
                        "subject": "Consulta sobre mi solicitud",
                        "status": "EN_PROCESO",
                        "status_label": "En proceso",
                        "priority": "MEDIA",
                        "priority_label": "Media",
                        "updated_at": "2026-10-10T14:30:00Z",
                        "assigned_advisor_name": "Asesor Demo",
                    }
                ],
                "ticket": None,
                "total": 1,
                "pagination": {"page": 1, "page_size": 5, "total_pages": 1},
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
            {
                "query_type": "TICKET_COUNT",
                "authentication_required": False,
                "authentication_actions": [],
                "found": True,
                "tickets": [],
                "ticket": None,
                "total": 3,
                "pagination": None,
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
            {
                "query_type": "TICKET_STATUS",
                "authentication_required": False,
                "authentication_actions": [],
                "found": True,
                "tickets": [],
                "ticket": {
                    "tracking_code": "TCK-20261010-AB12CD34EF",
                    "subject": "Consulta sobre mi solicitud",
                    "status": "EN_PROCESO",
                    "status_label": "En proceso",
                    "priority": "MEDIA",
                    "priority_label": "Media",
                    "updated_at": "2026-10-10T14:30:00Z",
                    "assigned_advisor_name": "Asesor Demo",
                },
                "total": None,
                "pagination": None,
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
            {
                "query_type": "ADVISOR_REPLY",
                "authentication_required": False,
                "authentication_actions": [],
                "found": True,
                "tickets": [],
                "ticket": {
                    "tracking_code": "TCK-20261010-AB12CD34EF",
                    "subject": "Consulta sobre mi solicitud",
                    "status": "EN_PROCESO",
                    "status_label": "En proceso",
                    "priority": "MEDIA",
                    "priority_label": "Media",
                    "updated_at": "2026-10-10T14:30:00Z",
                    "assigned_advisor_name": "Asesor Demo",
                },
                "total": None,
                "pagination": None,
                "advisor_responded": True,
                "latest_advisor_response": {
                    "author_name": "Asesor Demo",
                    "created_at": "2026-10-10T14:30:00Z",
                    "content": (
                        "Revisaremos tu solicitud y te informaremos el resultado."
                    ),
                },
            },
            {
                "query_type": "CLIENT_ACTION_REQUIRED",
                "authentication_required": False,
                "authentication_actions": [],
                "found": False,
                "tickets": [],
                "ticket": None,
                "total": 0,
                "pagination": {"page": 1, "page_size": 5, "total_pages": 0},
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
            {
                "query_type": "CLARIFY_TICKET_CODE",
                "authentication_required": False,
                "authentication_actions": [],
                "found": None,
                "tickets": [],
                "ticket": None,
                "total": None,
                "pagination": None,
                "advisor_responded": None,
                "latest_advisor_response": None,
            },
        ],
    )
    fallback_reason: str | None = None
    conversation_status: ConversationStatus = ConversationStatus.ACTIVE
    response_source: ChatbotResponseSource = ChatbotResponseSource.DETERMINISTIC
    sources: list["ChatbotSource"] = Field(default_factory=list)


class ConversationActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return " ".join(value.split()).strip() if value else None


class ChatFeedbackCreate(BaseModel):
    message_id: str | None = Field(default=None, max_length=36)
    is_helpful: bool
    escalation_accepted: bool | None = None
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return " ".join(value.split()).strip() if value else None


class ChatFeedbackRead(BaseModel):
    id: str
    message_id: str
    is_helpful: bool
    escalation_accepted: bool | None = None
    created_at: datetime


class ChatbotFeedbackMetrics(BaseModel):
    total_feedback: int
    helpful: int
    not_helpful: int
    usefulness_rate: float
    faq_backed: int
    generic_replies: int


class LinkConversationResponse(BaseModel):
    id: str
    user_id: str
    status: str
