import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.chatbot.config.persona import persona_text
from app.modules.chatbot.models.conversation import (
    TICKET_DRAFT_CONTEXT_KEY,
    Conversation,
    ConversationStatus,
)
from app.modules.chatbot.models.feedback import ChatbotFeedback
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.chatbot.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.chatbot.repositories.feedback_repository import (
    ChatbotFeedbackRepository,
)
from app.modules.chatbot.schemas.chatbot import (
    BotMessageResponse,
    ChatbotAdvisorResponse,
    ChatbotAuthenticationAction,
    ChatbotAvailableAction,
    ChatbotConvertedTicketSummary,
    ChatbotFeedbackMetrics,
    ChatbotIntent,
    ChatbotOperationalMetrics,
    ChatbotProactiveNotice,
    ChatbotProactiveNoticeType,
    ChatbotQuickReply,
    ChatbotSuggestedAction,
    ChatbotTicketAssignmentStatus,
    ChatbotTicketCard,
    ChatbotTicketDraft,
    ChatbotTicketDraftField,
    ChatbotTicketDraftStatus,
    ChatbotTicketPagination,
    ChatbotTicketQuery,
    ChatbotTicketQueryType,
    ChatbotUI,
    ChatbotUIAction,
    ChatbotUIOption,
    ChatbotUIType,
    ChatFeedbackCreate,
    ChatFeedbackRead,
    ChatMessageRead,
    ConversationRead,
)
from app.modules.chatbot.services.ai_tools import available_chatbot_tools
from app.modules.chatbot.services.guardrails import (
    detect_prompt_injection,
    has_verified_official_source,
    minimize_provider_input,
)
from app.modules.chatbot.services.rag_service import RAGService
from app.modules.chatbot.services.ticket_draft_service import (
    TicketDraftReply,
    TicketDraftService,
)
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.conocimiento.services.faq_content_policy import is_safe_unverified_faq
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.tickets.models.ticket import TicketPriority, TicketSource, TicketStatus
from app.modules.tickets.schemas.ticket import TicketRead
from app.modules.tickets.services.ticket_service import TicketService
from app.shared.text import normalize_text

_STOP_WORDS = {
    "a",
    "al",
    "como",
    "con",
    "consulta",
    "consultar",
    "de",
    "del",
    "dime",
    "el",
    "en",
    "es",
    "la",
    "las",
    "los",
    "me",
    "mi",
    "necesito",
    "no",
    "para",
    "por",
    "que",
    "cuanto",
    "cuanta",
    "cuantos",
    "cuantas",
    "son",
    "un",
    "una",
    "unos",
    "unas",
    "quiero",
    "y",
}
_FAQ_SUBCATEGORY_STOP_WORDS = _STOP_WORDS | {
    "abrir",
    "afiliar",
    "como",
    "configurar",
    "donde",
    "hacer",
    "habilitar",
    "ingresar",
    "mostrar",
    "olvidar",
    "puedo",
    "recuperar",
    "saber",
    "solicitar",
    "usar",
    "ver",
}
_FAQ_SUBCATEGORY_TAG_STOP_WORDS = {
    "ayuda",
    "atencion",
    "banco",
    "banca",
    "digital",
    "informacion",
    "servicio",
    "servicios",
    "solicitud",
}
_MARKUP_PATTERN = re.compile(r"<[^>]+>|javascript\s*:", re.IGNORECASE)
_ARITHMETIC_EXPRESSION_PATTERN = re.compile(
    r"(?<![\w-])\d+\s*(?:\+|\*|/|×|x|−)\s*\d+(?![\w-])|"
    r"(?<![\w-])\d+\s+-\s+\d+(?![\w-])",
    re.IGNORECASE,
)
_OBVIOUS_OUT_OF_SCOPE_PATTERN = re.compile(
    r"\b(?:clima|chiste|cultura\s+general|programar|programaci[oó]n|receta|"
    r"pel[ií]cula|canci[oó]n|presidente|presidenta|primer\s+ministro)\b|"
    r"\bcapital\s+de\s+(?:francia|per[uú]|espa[nñ]a|argentina|chile|colombia|"
    r"ecuador|bolivia|uruguay)\b",
    re.IGNORECASE,
)
_BANKING_OPERATION_PATTERN = re.compile(
    r"\b(?:saldos?|transferencias?|transferir|movimientos?)\b",
    re.IGNORECASE,
)
_TICKET_REQUEST_PATTERN = re.compile(
    r"\b(?:quiero|necesito|mejor|genera|generar|crea|crear|abre|abrir|"
    r"registra|registrar|levanta|solicito|solicitar|haz|hacer)\b"
    r".{0,40}\b(?:ticket|caso|solicitud|reclamo)\b|"
    r"\b(?:reportar|reporto|registrar)\b.{0,40}\b"
    r"(?:error|incidente|problema|falla)\b"
)
_ADVISOR_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:quiero|necesito|prefiero|solicito|deseo)\b"
        r".{0,45}\b(?:asesor|persona|agente|representante)\b"
    ),
    re.compile(
        r"\b(?:hablar|comunicarme|contactarme|conectarme)\b"
        r".{0,35}\b(?:asesor|persona|agente|representante)\b"
    ),
    re.compile(r"\batencion\s+humana\b"),
    re.compile(
        r"\b(?:quiero|quisiera|necesito|deseo|prefiero)\s+"
        r"(?:conversar|hablar)\s+con\s+(?:alguien|una\s+persona)\b"
    ),
)
_UNHELPFUL_FOLLOW_UP_PATTERN = re.compile(
    r"\b(?:no\s+me\s+(?:ayudo|sirvio|resolvio|respondio)|"
    r"no\s+fue\s+(?:util|suficiente)|no\s+me\s+ayuda|"
    r"no\s+(?:me\s+)?resuelves?\s*mi\s+duda|"
    r"no\s+resolviste\s+mi\s+duda|"
    r"no\s+entiendes?\s+(?:mi\s+)?(?:duda|consulta|pregunta)|"
    r"sigo\s+(?:confundid[oa]|sin\s+entender|sin\s+respuesta)|"
    r"eso\s+no\s+era\s+lo\s+que\s+(?:estaba\s+)?preguntando|"
    r"no\s+era\s+eso\s+lo\s+que\s+preguntaba)\b"
)
_GREETING_PHRASES = frozenset(
    {
        "hola",
        "buenas",
        "buenos dias",
        "buenas tardes",
        "hola buenas",
        "hola buenos dias",
        "hola buenas tardes",
    }
)
_FAQ_DISCOVERY_PHRASES = (
    "preguntas frecuentes",
    "que preguntas me puedes dar",
    "que preguntas te puedo hacer",
    "que preguntas puedo preguntarte",
    "que le puedo preguntar",
    "que puedo preguntarte",
    "que preguntas puedes responder",
    "que preguntas puedo hacer",
    "que puedo preguntar",
    "que temas puedes responder",
    "que temas puedes ayudar",
    "sobre que temas me puedes ayudar",
    "que consultas puedes resolver",
    "que consultas puedes atender",
    "que puedes hacer",
    "como me puedes ayudar",
)
_FAQ_CATEGORY_DISCOVERY_PHRASES = (
    "preguntas por categoria",
    "ver las preguntas por categoria",
    "que opciones hay",
    "que opciones tengo",
    "dame opciones",
    "dame las opciones",
    "que puedo consultar",
    "que puedo preguntar",
    "que categorias hay",
    "que categorias puedo consultar",
)
_NEW_CONSULTATION_PHRASES = (
    "otra consulta",
    "otra pregunta",
    "cambiar de tema",
    "consulta diferente",
    "empezar otra consulta",
)
_CATEGORY_HELP_TERMS = (
    "ayuda",
    "preguntas",
    "frecuentes",
    "faq",
    "opciones",
    "tema",
    "temas",
)
_BANKING_TOPIC_PATTERN = re.compile(
    r"\b(?:banco|banca|tarjetas?|cuentas?|cr[eé]ditos?|pr[eé]stamos?|"
    r"claves?|contrase[nñ]as?|reclamos?|solicitudes?)\b",
    re.IGNORECASE,
)
_FAQ_DISCOVERY_MAX_OPTIONS = 5
_FAQ_DISCOVERY_CATEGORY_ORDER = (
    "TARJETAS",
    "CUENTAS",
    "BANCA_DIGITAL",
    "CREDITOS",
    "RECLAMOS_SIMPLES",
)
_CONVERSATION_MENU_COMMANDS = frozenset(
    {
        "menu",
        "menu principal",
        "mostrar menu",
        "ver menu",
        "abrir menu",
        "inicio",
        "volver",
        "regresar",
        "volver al menu",
        "volver al inicio",
        "volver a las opciones",
        "regresar a las opciones",
        "regresar al menu",
        "regresar al inicio",
        "regresa al inicio",
        "quiero volver",
        "quiero regresar",
        "quiero volver al menu",
        "quiero volver al inicio",
        "mostrar opciones",
        "ver opciones",
        "retroceder",
        "volver atras",
        "quiero ver las opciones",
    }
)
_END_CONVERSATION_COMMANDS = frozenset(
    {
        "salir",
        "terminar",
        "finalizar",
        "cerrar chat",
        "cerrar",
        "cerrar conversacion",
        "finalizar chat",
        "finalizar conversacion",
        "terminar chat",
        "terminar conversacion",
        "quiero salir",
        "quiero terminar",
        "quiero finalizar",
        "quiero cerrar",
        "quiero cerrar el chat",
    }
)
_TRANSIENT_CONVERSATION_CONTEXT_KEYS = (
    "pending_ticket_query",
    "clarification_attempts",
    "pending_faq_ids",
    "pending_faq_category_ids",
    "pending_faq_category_id",
    "pending_faq_subcategories",
    "pending_faq_subcategory_id",
    "pending_ui_menu_id",
    "clarification_context",
    "last_ticket_query_codes",
    "last_ticket_query_index",
    "last_terms",
)


@dataclass(frozen=True)
class FAQMatch:
    faq: FAQ
    score: float
    intent: str | None
    category_name: str | None


@dataclass(frozen=True)
class TicketQueryRequest:
    action: ChatbotSuggestedAction
    query_type: ChatbotTicketQueryType
    tracking_code: str | None = None
    statuses: frozenset[TicketStatus] | None = None
    page: int = 1
    position: int | None = None
    requested_fields: frozenset[str] | None = None
    clarification_action: ChatbotSuggestedAction | None = None
    clarification_query_type: ChatbotTicketQueryType | None = None


_OPEN_TICKET_STATUSES = frozenset(
    status
    for status in TicketStatus
    if status not in {TicketStatus.CERRADO, TicketStatus.CANCELADO}
)
_TICKET_STATUS_LABELS = {
    TicketStatus.NUEVO: "Nuevo",
    TicketStatus.ASIGNADO: "Asignado",
    TicketStatus.EN_PROCESO: "En proceso",
    TicketStatus.PENDIENTE_CLIENTE: "Pendiente del cliente",
    TicketStatus.RESUELTO: "Resuelto",
    TicketStatus.CERRADO: "Cerrado",
    TicketStatus.CANCELADO: "Cancelado",
}
_TICKET_PRIORITY_LABELS = {
    TicketPriority.BAJA: "Baja",
    TicketPriority.MEDIA: "Media",
    TicketPriority.ALTA: "Alta",
    TicketPriority.URGENTE: "Urgente",
}
_TICKET_CODE_PATTERN = re.compile(r"\bTCK-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
_PROVIDER_TICKET_REFERENCE_PATTERN = re.compile(
    r"\bTCK-(?:[A-Z0-9]+(?:-[A-Z0-9]+)*|[.…-]+)?", re.IGNORECASE
)
_MAX_CHAT_TICKETS = 5


class ChatbotService:
    def __init__(
        self,
        conversation_repository: ConversationRepository | None = None,
        faq_repository: FAQRepository | None = None,
        notification_service: NotificationService | None = None,
        audit_service: AuditService | None = None,
        feedback_repository: ChatbotFeedbackRepository | None = None,
        ai_provider: object | None = None,
        rag_service: RAGService | None = None,
        ticket_service: TicketService | None = None,
        ticket_draft_service: TicketDraftService | None = None,
    ) -> None:
        self.conversations = conversation_repository or ConversationRepository()
        self.faqs = faq_repository or FAQRepository()
        self.notifications = notification_service or NotificationService()
        self.audit = audit_service or AuditService()
        self.chat_feedback = feedback_repository or ChatbotFeedbackRepository()
        self.rag = rag_service or RAGService(provider=ai_provider)
        self.tickets = ticket_service or TicketService()
        self.ticket_drafts = ticket_draft_service or TicketDraftService()

    @staticmethod
    def normalize_text(value: str) -> str:
        return normalize_text(value)

    def create_conversation(
        self,
        session: Session,
        current_user: AuthenticatedUser | None,
        *,
        anonymous_client_ip: str | None = None,
    ) -> Conversation:
        self._require_public_actor(current_user)
        user_id = current_user.user.id if current_user else None
        anonymous_key = None
        if user_id is None:
            anonymous_key = self._anonymous_key(anonymous_client_ip)
            since = datetime.now(UTC) - timedelta(hours=1)
            if (
                self.conversations.count_recent_anonymous_conversations(
                    session, anonymous_key, since
                )
                >= get_settings().chatbot_max_anonymous_conversations_per_hour
            ):
                raise HTTPException(
                    status_code=429,
                    detail="Se alcanzó el límite de conversaciones anónimas",
                )
        conversation = self.conversations.add_conversation(
            session, Conversation(user_id=user_id, anonymous_key=anonymous_key)
        )
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="CONVERSATION_CREATED",
            actor_user_id=user_id,
            actor_role=current_user.role if current_user else None,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=user_id,
            success=True,
            metadata={"anonymous": user_id is None},
        )
        session.commit()
        session.refresh(conversation)
        return conversation

    def get_conversation(
        self,
        session: Session,
        conversation_id: str,
        current_user: AuthenticatedUser | None,
    ) -> ConversationRead:
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        return self._conversation_read(session, conversation, current_user)

    def send_message(
        self,
        session: Session,
        conversation_id: str,
        content: str | None,
        current_user: AuthenticatedUser | None,
        *,
        option_id: str | None = None,
    ) -> BotMessageResponse:
        self._require_public_actor(current_user)
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        self._ensure_message_allowed(conversation)
        navigation_target = None
        selected_faq_subcategory = None
        if option_id is not None and option_id.startswith("nav:"):
            navigation_target = self._resolve_faq_navigation_target(
                conversation, option_id
            )
            content = {
                "main": "mostrar menú",
                "faq_categories": "volver a categorías de preguntas frecuentes",
                "faq_subcategories": "volver a temas de preguntas frecuentes",
            }[navigation_target]
        elif option_id is not None and option_id.startswith("subcategory:"):
            selected_faq_subcategory = self._resolve_faq_subcategory_option(
                session, conversation, option_id
            )
            content = str(selected_faq_subcategory["label"])
        elif option_id is not None:
            content = self._resolve_option_id(session, conversation, option_id)
        if content is None:
            raise HTTPException(status_code=422, detail="Mensaje no válido")
        clean_content = self._clean_content(content)
        deterministic_intent = self._classify_deterministic_intent(clean_content)
        is_faq_category_request = option_id == "menu:faq"
        selected_faq_category_id = (
            option_id.removeprefix("category:")
            if option_id is not None and option_id.startswith("category:")
            else None
        )
        requested_faq_category_id = (
            None
            if selected_faq_category_id is not None
            or selected_faq_subcategory is not None
            else self._requested_faq_category_id(session, clean_content)
        )
        is_faq_category_discovery = (
            requested_faq_category_id is None
            and self._is_faq_category_discovery_request(clean_content)
        )
        is_new_consultation = self._is_new_consultation_request(clean_content)
        is_faq_discovery = (
            not is_faq_category_request
            and selected_faq_category_id is None
            and selected_faq_subcategory is None
            and requested_faq_category_id is None
            and not is_faq_category_discovery
            and not is_new_consultation
            and navigation_target is None
            and self._is_faq_discovery_request(clean_content)
        )
        is_banking_operation = self._is_banking_operation_request(clean_content)
        contains_arithmetic = self._contains_arithmetic_expression(clean_content)
        has_banking_topic = bool(_BANKING_TOPIC_PATTERN.search(clean_content))
        is_mixed_banking_arithmetic = contains_arithmetic and has_banking_topic
        is_out_of_scope_request = not is_banking_operation and (
            self._is_obviously_out_of_scope(clean_content)
            or (contains_arithmetic and not has_banking_topic)
        )
        lifecycle_action = (
            ChatbotSuggestedAction.SHOW_MENU
            if navigation_target == "main"
            else None
            if (
                navigation_target is not None
                or is_faq_discovery
                or is_faq_category_discovery
                or is_new_consultation
                or requested_faq_category_id is not None
                or is_out_of_scope_request
            )
            else self._conversation_lifecycle_action(clean_content)
        )
        if lifecycle_action is None:
            self._enforce_limits(session, conversation)
        recent_messages = self.conversations.list_recent_messages(
            session, conversation.id
        )
        previous_user_message = next(
            (
                message
                for message in reversed(recent_messages)
                if message.sender_type == SenderType.USER
            ),
            None,
        )
        previous_bot_message = next(
            (
                message
                for message in reversed(recent_messages)
                if message.sender_type == SenderType.BOT
            ),
            None,
        )
        was_waiting_for_clarification = (
            conversation.status == ConversationStatus.WAITING_CLARIFICATION
        )
        saved_context = dict(conversation.context_json or {})
        is_advisor_request = self._is_advisor_request(clean_content)
        advisor_ticket_enabled = get_settings().chatbot_escalation_ticket_enabled
        ticket_query_request = self._ticket_query_request(clean_content)
        ticket_query_request = self._resume_clarified_ticket_query(
            clean_content, conversation, ticket_query_request
        )
        may_use_ticket_context = ticket_query_request is None or (
            ticket_query_request.query_type
            in {
                ChatbotTicketQueryType.CLARIFY_TICKET_CODE,
                ChatbotTicketQueryType.TICKET_STATUS,
                ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS,
                ChatbotTicketQueryType.ADVISOR_REPLY,
            }
            and ticket_query_request.tracking_code is None
            and ticket_query_request.position is None
        )
        if (
            may_use_ticket_context
            and get_settings().chatbot_contextual_followups_enabled
        ):
            contextual_request = self._contextual_ticket_query_request(
                clean_content, saved_context
            )
            if contextual_request is not None:
                ticket_query_request = contextual_request
        is_ticket_request = (
            self._is_ticket_request(clean_content) and ticket_query_request is None
        )
        is_unhelpful_follow_up = bool(
            previous_bot_message and self._is_unhelpful_follow_up(clean_content)
        )
        is_greeting = self._is_pure_greeting(clean_content)
        saved_draft = saved_context.get(TICKET_DRAFT_CONTEXT_KEY)
        current_draft = self.ticket_drafts.read(session, saved_draft)
        paused_draft = saved_context.get("ticket_draft_paused") is True
        resume_paused_draft = paused_draft and self.ticket_drafts.is_resume_request(
            clean_content
        )
        resume_after_authentication = bool(
            current_user is not None
            and previous_bot_message is not None
            and previous_bot_message.intent
            in {
                "TICKET_CREATION_AUTHENTICATION_REQUIRED",
                "ADVISOR_REQUEST_AUTHENTICATION_REQUIRED",
            }
            and self.ticket_drafts.is_resume_request(clean_content)
        )
        saved_advisor_handoff = saved_context.get("advisor_handoff_requested") is True
        advisor_handoff_requested = advisor_ticket_enabled and (
            is_advisor_request
            or (
                saved_advisor_handoff
                and (
                    resume_paused_draft
                    or resume_after_authentication
                    or (
                        not paused_draft
                        and current_draft.status
                        in {
                            ChatbotTicketDraftStatus.COLLECTING,
                            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                            ChatbotTicketDraftStatus.CONFIRMED,
                        }
                    )
                )
            )
        )
        advisor_summary = None
        if advisor_handoff_requested:
            saved_summary = saved_context.get("advisor_summary")
            advisor_summary = (
                saved_summary
                if isinstance(saved_summary, str)
                else self._conversation_summary(recent_messages, clean_content)
            )
        start_ticket_draft = (
            is_ticket_request
            or advisor_handoff_requested
            or resume_after_authentication
            or resume_paused_draft
        )
        ticket_domain_message = bool(
            re.search(
                r"\b(?:tickets?|solicitudes?|reclamos?|casos?)\b",
                normalize_text(clean_content),
            )
        )
        clear_faq_question = self._is_clear_faq_question(clean_content)
        active_draft = current_draft.status in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
            ChatbotTicketDraftStatus.CONFIRMED,
        }
        draft_topic_clarification = bool(
            active_draft
            and not paused_draft
            and not start_ticket_draft
            and ticket_query_request is None
            and not ticket_domain_message
            and not clear_faq_question
            and (
                "?" in clean_content
                or clean_content.lstrip().startswith("¿")
                or re.search(
                    r"\b(?:no entiendo|no me queda claro|puedes aclararme|"
                    r"me puedes aclarar|puedes explicarme|me puedes explicar|"
                    r"a que te refieres)\b",
                    normalize_text(clean_content),
                )
            )
        )
        unrelated_special_message = lifecycle_action is not None or (
            not start_ticket_draft
            and (
                is_greeting
                or is_faq_discovery
                or is_faq_category_discovery
                or is_new_consultation
                or requested_faq_category_id is not None
                or is_out_of_scope_request
                or is_faq_category_request
                or selected_faq_category_id is not None
                or is_unhelpful_follow_up
                or ticket_query_request is not None
                or ticket_domain_message
                or clear_faq_question
                or draft_topic_clarification
            )
        )
        ticket_draft_flow = (
            not unrelated_special_message
            and not (
                paused_draft and not resume_paused_draft and not start_ticket_draft
            )
            and self.ticket_drafts.should_handle(
                session,
                clean_content,
                current_draft,
                start_requested=start_ticket_draft,
            )
        )

        orchestration_outcome = None
        orchestration_tool_name: str | None = None
        search_query_override: str | None = None
        confirmation_text = normalize_text(clean_content).strip(" .,!?:;¿¡")
        orchestration_user_message = clean_content
        if (
            is_unhelpful_follow_up
            and previous_user_message is not None
            and previous_bot_message is not None
            and self._is_public_context_message(previous_bot_message)
        ):
            orchestration_user_message = self._contextual_provider_input(
                previous_user_message.content,
                previous_bot_message.content,
                clean_content,
            )
        orchestration_intent = (
            ChatbotIntent.TICKET_DRAFT_UPDATE
            if ticket_draft_flow or start_ticket_draft
            else ChatbotIntent.TICKET_QUERY
            if ticket_query_request is not None or ticket_domain_message
            else ChatbotIntent.GREETING
            if is_greeting
            else ChatbotIntent.SHOW_MENU
            if lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
            else ChatbotIntent.FAQ_DISCOVERY
            if is_faq_discovery
            else ChatbotIntent.FAQ_CATEGORY_DISCOVERY
            if is_faq_category_discovery or is_faq_category_request
            else ChatbotIntent.FAQ_SUBCATEGORY_DISCOVERY
            if requested_faq_category_id is not None
            or selected_faq_category_id is not None
            or selected_faq_subcategory is not None
            else ChatbotIntent.CLARIFICATION
            if is_unhelpful_follow_up
            else ChatbotIntent.OUT_OF_SCOPE
            if is_out_of_scope_request
            else ChatbotIntent.REQUEST_ADVISOR
            if is_advisor_request
            else None
        )
        orchestration_should_run = (
            get_settings().chatbot_ai_enabled
            and option_id is None
            and lifecycle_action != ChatbotSuggestedAction.END_CONVERSATION
            and not is_banking_operation
            and not is_mixed_banking_arithmetic
            and orchestration_intent is not None
            and not draft_topic_clarification
            and not (
                current_draft.status == ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION
                and confirmation_text
                in {"si", "ok", "de acuerdo", "listo", "confirmo", "correcto"}
            )
        )
        if orchestration_should_run:
            recent_provider_calls = (
                self.conversations.count_recent_ai_provider_attempts(
                    session,
                    conversation.id,
                    datetime.now(UTC) - timedelta(minutes=1),
                )
            )
            actor_provider_calls = (
                self.conversations.count_recent_ai_provider_attempts_for_actor(
                    session,
                    user_id=conversation.user_id,
                    anonymous_key=conversation.anonymous_key,
                    since=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            prior_ai_responses = self.conversations.count_ai_provider_attempts(
                session, conversation.id
            )
            if orchestration_intent == ChatbotIntent.TICKET_QUERY:
                provider_turn = (
                    "El usuario consulta sus tickets propios. "
                    "Selecciona una herramienta compatible."
                )
            elif orchestration_intent == ChatbotIntent.TICKET_DRAFT_UPDATE:
                provider_turn = (
                    "El usuario quiere iniciar un borrador de ticket."
                    if start_ticket_draft
                    else "El usuario quiere actualizar un borrador de ticket."
                )
            elif orchestration_intent == ChatbotIntent.REQUEST_ADVISOR:
                provider_turn = (
                    "El usuario solicita hablar con un asesor. "
                    "Selecciona una herramienta compatible."
                )
            else:
                provider_turn = _PROVIDER_TICKET_REFERENCE_PATTERN.sub(
                    "referencia de ticket", orchestration_user_message
                )
            orchestration_outcome = self.rag.generate(
                conversation_id=conversation.id,
                user_message=provider_turn,
                matches=[],
                requests_last_minute=recent_provider_calls,
                requests_by_actor_last_hour=actor_provider_calls,
                responses_in_conversation=prior_ai_responses,
                grounding_required=False,
                tools=available_chatbot_tools(),
                conversation_context=self._provider_dialogue_context(
                    recent_messages,
                    saved_context,
                    current_draft,
                    include_user_turns=orchestration_intent
                    not in {
                        ChatbotIntent.TICKET_QUERY,
                        ChatbotIntent.TICKET_DRAFT_UPDATE,
                    },
                ),
                response_mode="orchestration",
                conversational_intent=orchestration_intent,
            )
            if orchestration_outcome.accepted and orchestration_outcome.tool_call:
                orchestration_tool_name = orchestration_outcome.tool_call.name
                tool_arguments = orchestration_outcome.tool_call.arguments
                if orchestration_tool_name == "query_my_tickets":
                    proposed_query = self._ticket_query_from_tool_call(
                        clean_content,
                        tool_arguments,
                        saved_context,
                        ticket_query_request,
                    )
                    if proposed_query is not None:
                        ticket_query_request = proposed_query
                        ticket_draft_flow = False
                        start_ticket_draft = False
                        lifecycle_action = None
                elif orchestration_tool_name in {
                    "start_ticket_draft",
                    "update_ticket_draft",
                }:
                    may_update_draft = (
                        ticket_draft_flow
                        or start_ticket_draft
                        or (
                            orchestration_tool_name == "start_ticket_draft"
                            and self._explicit_ticket_creation_request(clean_content)
                        )
                    )
                    if may_update_draft:
                        start_ticket_draft = start_ticket_draft or (
                            orchestration_tool_name == "start_ticket_draft"
                        )
                        is_ticket_request = True
                        ticket_draft_flow = True
                        lifecycle_action = None
                elif orchestration_tool_name == "show_options":
                    may_show_options = bool(
                        lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
                        or is_faq_discovery
                        or is_faq_category_discovery
                        or is_faq_category_request
                        or is_new_consultation
                        or requested_faq_category_id is not None
                        or selected_faq_category_id is not None
                        or selected_faq_subcategory is not None
                        or re.search(
                            r"\b(?:opciones?|menu|ayuda|temas|categorias)\b",
                            normalize_text(clean_content),
                        )
                    )
                    if may_show_options:
                        lifecycle_action = ChatbotSuggestedAction.SHOW_MENU
                        ticket_draft_flow = False
                elif orchestration_tool_name == "search_public_faq":
                    query = tool_arguments.get("query")
                    if isinstance(query, str) and self._supported_provider_query(
                        query, clean_content
                    ):
                        search_query_override = query

        if (
            current_user is not None
            and conversation.user_id is None
            and (ticket_query_request is not None or ticket_draft_flow)
        ):
            self.link_user(session, conversation.id, current_user)

        user_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.USER,
                content=clean_content,
            ),
        )
        search_content = search_query_override or clean_content
        ai_user_message = (
            "Consulta o actualización de tickets sin datos privados."
            if (
                ticket_query_request is not None
                or ticket_draft_flow
                or is_ticket_request
                or ticket_domain_message
                or is_advisor_request
            )
            else clean_content
        )
        if (
            ai_user_message == clean_content
            and is_unhelpful_follow_up
            and previous_user_message is not None
            and previous_bot_message is not None
            and self._is_public_context_message(previous_bot_message)
        ):
            ai_user_message = self._contextual_provider_input(
                previous_user_message.content,
                previous_bot_message.content,
                clean_content,
            )
        selected_match: FAQMatch | None = None
        matches: list[FAQMatch] = []
        retrieval_unavailable = False
        ticket_draft_reply: TicketDraftReply | None = None
        try:
            if lifecycle_action is not None:
                if (
                    lifecycle_action == ChatbotSuggestedAction.END_CONVERSATION
                    and current_draft.status
                    in {
                        ChatbotTicketDraftStatus.COLLECTING,
                        ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                        ChatbotTicketDraftStatus.CONFIRMED,
                    }
                ):
                    ticket_draft_reply = self.ticket_drafts.handle(
                        session,
                        (
                            "Cancela el borrador del ticket. "
                            "No deseo continuar con su creación."
                        ),
                        saved_draft,
                        start_requested=False,
                    )
                result = self._conversation_lifecycle_response(
                    lifecycle_action,
                    draft_paused=(
                        lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
                        and current_draft.status
                        in {
                            ChatbotTicketDraftStatus.COLLECTING,
                            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                            ChatbotTicketDraftStatus.CONFIRMED,
                        }
                    ),
                    draft_cancelled=(
                        lifecycle_action == ChatbotSuggestedAction.END_CONVERSATION
                        and current_draft.status
                        in {
                            ChatbotTicketDraftStatus.COLLECTING,
                            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                            ChatbotTicketDraftStatus.CONFIRMED,
                        }
                    ),
                )
            elif navigation_target == "faq_categories":
                result = self._faq_category_discovery_response(session)
            elif navigation_target == "faq_subcategories":
                category_id = (conversation.context_json or {}).get(
                    "pending_faq_category_id"
                )
                result, matches = self._faq_subcategory_discovery_response(
                    session, category_id=str(category_id)
                )
            elif is_faq_category_request:
                result = self._faq_category_discovery_response(session)
            elif selected_faq_category_id is not None:
                result, matches = self._faq_subcategory_discovery_response(
                    session, category_id=selected_faq_category_id
                )
            elif selected_faq_subcategory is not None:
                selected_faq_ids = {
                    faq_id
                    for faq_id in selected_faq_subcategory.get("faq_ids", [])
                    if isinstance(faq_id, str)
                }
                result, matches = self._faq_discovery_response(
                    session, faq_ids=selected_faq_ids
                )
                result["candidate_faq_category_id"] = selected_faq_subcategory.get(
                    "category_id"
                )
                result["candidate_faq_subcategory_id"] = selected_faq_subcategory.get(
                    "id"
                )
            elif is_banking_operation:
                result = self._banking_operation_response()
            elif ticket_query_request is not None:
                result = self._ticket_query_response(
                    session, current_user, ticket_query_request
                )
            elif not advisor_ticket_enabled and (
                is_advisor_request
                or saved_context.get("advisor_handoff_requested") is True
            ):
                result = self._advisor_handoff_unavailable_response()
            elif ticket_draft_flow and current_user is None:
                result = self._ticket_authentication_response(
                    conversation, for_advisor=advisor_handoff_requested
                )
            elif ticket_draft_flow:
                if conversation.user_id is None:
                    self.link_user(session, conversation.id, current_user)
                ticket_draft_reply = self.ticket_drafts.handle(
                    session,
                    clean_content,
                    saved_draft,
                    start_requested=start_ticket_draft,
                    suggested_category_id=(
                        conversation.category_id
                        if start_ticket_draft
                        and (
                            advisor_handoff_requested
                            or (
                                is_ticket_request
                                and previous_bot_message is not None
                                and previous_bot_message.faq_id is not None
                            )
                        )
                        else None
                    ),
                    initial_description=advisor_summary,
                )
                if ticket_draft_reply.cancelled:
                    advisor_handoff_requested = False
                result = self._ticket_draft_response(
                    conversation,
                    ticket_draft_reply,
                    advisor_handoff=advisor_handoff_requested,
                )
            elif draft_topic_clarification:
                reply = TicketDraftReply(
                    draft=current_draft,
                    answer=(
                        "El borrador sigue guardado. ¿Quieres continuar con el "
                        "borrador o cambiar de tema?"
                    ),
                    requires_clarification=True,
                    offers_ticket=False,
                    ready=False,
                )
                result = self._ticket_draft_response(conversation, reply)
                result["options"] = [
                    "Continuar con el borrador",
                    "Cambiar de tema",
                ]
                result["draft_topic_clarification"] = True
            elif is_out_of_scope_request:
                result = self._out_of_scope_response(session)
            elif is_new_consultation or is_faq_category_discovery:
                result = self._faq_category_discovery_response(session)
            elif requested_faq_category_id is not None:
                result, matches = self._faq_subcategory_discovery_response(
                    session, category_id=requested_faq_category_id
                )
            elif is_unhelpful_follow_up:
                result = self._unhelpful_follow_up_response(
                    conversation,
                    exhausted=(
                        int(saved_context.get("clarification_attempts", 0))
                        >= get_settings().chatbot_max_clarification_attempts
                    ),
                )
                result["context_seed"] = (
                    previous_user_message.content
                    if previous_user_message is not None
                    else None
                )
            elif is_greeting:
                result = self._greeting_response()
            elif is_faq_discovery:
                result, matches = self._faq_discovery_response(session)
            else:
                selected_match = (
                    self._resolve_pending_selection(
                        session, conversation, clean_content
                    )
                    if was_waiting_for_clarification
                    else None
                )
                if selected_match is not None:
                    matches = [selected_match]
                else:
                    matches = self._find_matches(session, search_content)
                    previous_clarification = (conversation.context_json or {}).get(
                        "clarification_context", []
                    )
                    if (
                        was_waiting_for_clarification
                        and isinstance(previous_clarification, list)
                        and previous_clarification
                        and self._is_contextual_follow_up(clean_content)
                    ):
                        bounded_history = [
                            item[:250]
                            for item in previous_clarification[-2:]
                            if isinstance(item, str)
                        ]
                        contextual_content = " ".join([*bounded_history, clean_content])
                        contextual_matches = self._find_matches(
                            session, contextual_content
                        )
                        if contextual_matches and (
                            not matches
                            or contextual_matches[0].score > matches[0].score
                        ):
                            search_content = contextual_content
                            matches = contextual_matches
                    elif (
                        previous_bot_message is not None
                        and previous_user_message is not None
                        and not self._is_ticket_query_intent(
                            previous_bot_message.intent
                        )
                        and self._is_referential_follow_up(clean_content)
                    ):
                        contextual_content = previous_user_message.content[:250]
                        contextual_matches = self._find_matches(
                            session, contextual_content
                        )
                        if contextual_matches and (
                            not matches
                            or contextual_matches[0].score > matches[0].score
                        ):
                            search_content = contextual_content
                            matches = contextual_matches
                        ai_user_message = self._contextual_provider_input(
                            previous_user_message.content,
                            previous_bot_message.content,
                            clean_content,
                        )
                result = self._response_for_matches(conversation, matches)
        except Exception:
            retrieval_unavailable = True
            matches = []
            selected_match = None
            result = self._knowledge_unavailable_response()

        if lifecycle_action is not None:
            pass
        elif result.get("fallback_reason") == "LOW_CONFIDENCE":
            try:
                discovery, _ = self._faq_discovery_response(session)
                result["options"] = discovery["options"]
                result["candidate_faq_ids"] = discovery["candidate_faq_ids"]
                result["sources"] = discovery["sources"]
            except Exception:
                result["options"] = []
            result["available_actions"] = [
                ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                ChatbotAvailableAction.CREATE_TICKET,
                ChatbotAvailableAction.CONTACT_ADVISOR,
                ChatbotAvailableAction.VIEW_MY_TICKETS,
            ]
        elif result.get("fallback_reason") == "KNOWLEDGE_UNAVAILABLE":
            result["available_actions"] = [
                ChatbotAvailableAction.RETRY,
                ChatbotAvailableAction.CREATE_TICKET,
                ChatbotAvailableAction.CONTACT_ADVISOR,
                ChatbotAvailableAction.OFFICIAL_CHANNELS,
            ]
        elif result.get("intent") == "FOLLOW_UP_UNRESOLVED":
            if result.get("requires_clarification"):
                try:
                    discovery, _ = self._faq_discovery_response(session)
                    result["options"] = discovery["options"]
                    result["candidate_faq_ids"] = discovery["candidate_faq_ids"]
                    result["sources"] = discovery["sources"]
                except Exception:
                    result["options"] = []
            result["available_actions"] = [
                ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                ChatbotAvailableAction.CREATE_TICKET,
                ChatbotAvailableAction.CONTACT_ADVISOR,
            ]
        elif (
            result.get("requires_clarification")
            and not result.get("options")
            and not ticket_draft_flow
            and ticket_query_request is None
            and not is_faq_discovery
        ):
            try:
                discovery, _ = self._faq_discovery_response(session)
                result["options"] = discovery["options"]
                result["candidate_faq_ids"] = discovery["candidate_faq_ids"]
                result["sources"] = discovery["sources"]
            except Exception:
                result["options"] = []
            result["available_actions"] = [
                ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                ChatbotAvailableAction.CREATE_TICKET,
                ChatbotAvailableAction.CONTACT_ADVISOR,
            ]

        result["response_source"] = result.get("response_source", "DETERMINISTIC")
        result["sources"] = result.get("sources", self._public_sources(matches[:1]))
        conversational_ai = bool(
            is_unhelpful_follow_up
            or is_greeting
            or is_faq_discovery
            or is_faq_category_discovery
            or is_new_consultation
            or requested_faq_category_id is not None
            or is_out_of_scope_request
            or (
                lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
                and option_id is None
            )
            or (
                result.get("requires_clarification")
                and not matches
                and navigation_target is None
                and lifecycle_action is None
            )
        )
        orchestration_text_action = bool(
            orchestration_outcome is not None
            and orchestration_outcome.accepted
            and orchestration_outcome.answer
            and (
                (
                    orchestration_tool_name in {"respond", "ask_clarification"}
                    and conversational_ai
                )
                or (
                    orchestration_tool_name == "show_options"
                    and (
                        lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
                        or bool(result.get("options"))
                    )
                )
                or (
                    orchestration_tool_name
                    in {"start_ticket_draft", "update_ticket_draft"}
                    and ticket_draft_reply is not None
                    and self._draft_answer_matches_missing_field(
                        orchestration_outcome.answer,
                        ticket_draft_reply.draft,
                    )
                )
                or (orchestration_tool_name is None and conversational_ai)
            )
        )
        orchestration_primary_response = bool(
            orchestration_should_run
            and orchestration_intent
            in {
                ChatbotIntent.GREETING,
                ChatbotIntent.SHOW_MENU,
                ChatbotIntent.FAQ_DISCOVERY,
                ChatbotIntent.FAQ_CATEGORY_DISCOVERY,
                ChatbotIntent.FAQ_SUBCATEGORY_DISCOVERY,
                ChatbotIntent.CLARIFICATION,
                ChatbotIntent.OUT_OF_SCOPE,
                ChatbotIntent.REQUEST_ADVISOR,
            }
            and search_query_override is None
        )
        skip_ai_reasons = (
            (retrieval_unavailable, "KNOWLEDGE_UNAVAILABLE"),
            (is_banking_operation, "BANKING_OPERATION_OUT_OF_SCOPE"),
            (ticket_query_request is not None, "TICKET_QUERY"),
            (is_advisor_request and is_ticket_request, "TICKET_ADVISOR_FLOW"),
            (is_advisor_request, "ADVISOR_FLOW"),
            (is_ticket_request, "TICKET_DRAFT_FLOW"),
            (is_mixed_banking_arithmetic, "MIXED_BANKING_AND_ARITHMETIC"),
            (is_faq_category_request, "FAQ_CATEGORY_DISCOVERY"),
            (selected_faq_category_id is not None, "FAQ_CATEGORY_SELECTION"),
            (selected_faq_subcategory is not None, "FAQ_SUBCATEGORY_SELECTION"),
            (navigation_target is not None, "FAQ_NAVIGATION"),
            (
                not paused_draft
                and current_draft.status
                in {
                    ChatbotTicketDraftStatus.COLLECTING,
                    ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                    ChatbotTicketDraftStatus.CONFIRMED,
                },
                "ACTIVE_TICKET_DRAFT",
            ),
            (ticket_draft_reply is not None, "TICKET_DRAFT_REPLY"),
            (
                lifecycle_action == ChatbotSuggestedAction.END_CONVERSATION,
                "CONVERSATION_LIFECYCLE",
            ),
            (orchestration_text_action, "ORCHESTRATION_RESPONSE"),
            (orchestration_primary_response, "ORCHESTRATION_RESPONSE"),
        )
        ai_skip_reason = next(
            (reason for should_skip, reason in skip_ai_reasons if should_skip),
            None,
        )
        skip_ai = ai_skip_reason is not None
        ai_outcomes = [
            orchestration_outcome for _ in (0,) if orchestration_outcome is not None
        ]
        ai_outcome = None
        if skip_ai:
            deterministic_trace: dict[str, object] = {
                "provider_called": False,
                "routing_decision": "DETERMINISTIC_SKIP",
                "routing_reason": ai_skip_reason,
                **(
                    {"fallback": True, "fallback_reason": "KNOWLEDGE_UNAVAILABLE"}
                    if retrieval_unavailable
                    else {}
                ),
            }
            if orchestration_tool_name is not None:
                deterministic_trace["orchestration_tool"] = orchestration_tool_name
            if orchestration_text_action and orchestration_outcome is not None:
                result["answer"] = orchestration_outcome.answer
                result["response_source"] = "AI"
                result["sources"] = []
                result["fallback_reason"] = None
            if orchestration_tool_name is not None:
                deterministic_trace["orchestration_tool"] = orchestration_tool_name
            result["ai_trace"] = self._merge_ai_traces(
                deterministic_trace,
                orchestration_outcome.trace
                if orchestration_outcome is not None
                else None,
                ai_outcome.trace if ai_outcome is not None else None,
            )
        else:
            provider_calls_by_actor_last_hour = (
                self.conversations.count_recent_ai_provider_attempts_for_actor(
                    session,
                    user_id=conversation.user_id,
                    anonymous_key=conversation.anonymous_key,
                    since=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            requests_last_minute = self.conversations.count_recent_ai_provider_attempts(
                session,
                conversation.id,
                datetime.now(UTC) - timedelta(minutes=1),
            )
            responses_in_conversation = self.conversations.count_ai_provider_attempts(
                session, conversation.id
            )
            orchestration_calls = self._provider_call_count(
                orchestration_outcome.trace
                if orchestration_outcome is not None
                else None
            )
            requests_last_minute += orchestration_calls
            provider_calls_by_actor_last_hour += orchestration_calls
            responses_in_conversation += orchestration_calls
            retrieval_trace: dict[str, object] = {}
            if not conversational_ai and self.rag.embedding_provider is not None:
                retrieval_candidates = self._retrieval_candidates(session, matches)
                matches, retrieval_trace = self.rag.retrieve_hybrid(
                    user_message=ai_user_message,
                    lexical_matches=matches,
                    candidates=retrieval_candidates,
                    requests_last_minute=requests_last_minute,
                    requests_by_actor_last_hour=provider_calls_by_actor_last_hour,
                    responses_in_conversation=responses_in_conversation,
                )
            ai_outcome = self.rag.generate(
                conversation_id=conversation.id,
                user_message=ai_user_message,
                matches=matches,
                requests_last_minute=requests_last_minute,
                requests_by_actor_last_hour=provider_calls_by_actor_last_hour,
                responses_in_conversation=responses_in_conversation,
                grounding_required=not conversational_ai,
                conversational_intent=(
                    self._conversational_intent_for_result(
                        result,
                        lifecycle_action=lifecycle_action,
                        is_greeting=is_greeting,
                        is_out_of_scope_request=is_out_of_scope_request,
                    )
                    if conversational_ai
                    else None
                ),
            )
            ai_outcomes.append(ai_outcome)
            ai_outcome.trace.update(retrieval_trace)
            if orchestration_tool_name is not None:
                ai_outcome.trace["orchestration_tool"] = orchestration_tool_name
            result["ai_trace"] = self._merge_ai_traces(
                orchestration_outcome.trace
                if orchestration_outcome is not None
                else None,
                ai_outcome.trace,
            )
            if ai_outcome.accepted:
                result["answer"] = ai_outcome.answer
                result["response_source"] = "AI"
                result["intent_classification"] = ai_outcome.intent_classification
                result["sources"] = self._public_sources(ai_outcome.sources)
                if conversational_ai:
                    result["fallback_reason"] = None
                if not conversational_ai:
                    selected_source_ids = {
                        source.source_id for source in ai_outcome.sources
                    }
                    selected_match = next(
                        (
                            match
                            for match in matches
                            if match.faq.id in selected_source_ids
                        ),
                        matches[0] if matches else None,
                    )
                    if selected_match is not None:
                        result["match"] = selected_match
                        result["faq_id"] = selected_match.faq.id
                        result["intent"] = selected_match.intent
                        result["confidence"] = selected_match.score
                        result["category_name"] = selected_match.category_name or (
                            self._category_name(session, selected_match.faq.category_id)
                        )
                    result["resolved"] = True
                    result["offers_ticket"] = False
                    result["requires_clarification"] = False
                    result["fallback_reason"] = None
                    result["options"] = []
                    result["candidate_faq_ids"] = []
                    result["available_actions"] = []
            elif ai_outcome.trace.get("provider_status") == "GENERATED":
                result["fallback_reason"] = ai_outcome.reason

        if ai_outcomes:
            audit_outcome = ai_outcomes[-1]
            action = (
                "AI_ACCEPTED"
                if audit_outcome.accepted
                else (
                    "AI_GUARDRAIL_REJECTED"
                    if audit_outcome.reason
                    in {
                        "USER_INJECTION",
                        "SENSITIVE_REQUEST",
                        "CONTEXT_INJECTION",
                        "UNSAFE_OUTPUT",
                        "INVALID_SOURCES",
                        "OUT_OF_CONTEXT",
                        "OUTPUT_SIZE",
                        "UNGROUNDED_CLAIM",
                        "OUT_OF_SCOPE_REDIRECT_REQUIRED",
                    }
                    else "AI_FALLBACK"
                )
            )
            self.audit.record(
                session,
                event_type="CHATBOT_AI",
                action=action,
                actor_user_id=conversation.user_id,
                actor_role=current_user.role if current_user else None,
                resource_type="CONVERSATION",
                resource_id=conversation.id,
                target_user_id=conversation.user_id,
                success=audit_outcome.accepted or action == "AI_FALLBACK",
                error_code=None if audit_outcome.accepted else audit_outcome.reason,
                metadata={
                    "provider": audit_outcome.trace.get("provider"),
                    "model": audit_outcome.trace.get("model"),
                    "source_count": len(audit_outcome.sources),
                    "input_tokens": result["ai_trace"].get("input_tokens"),
                    "output_tokens": result["ai_trace"].get("output_tokens"),
                    "total_tokens": result["ai_trace"].get("total_tokens"),
                    "fallback": not audit_outcome.accepted,
                    "fallback_reason": audit_outcome.trace.get("fallback_reason"),
                    "validation_subrule": audit_outcome.trace.get("validation_subrule"),
                    "response_source": result["ai_trace"].get(
                        "response_source",
                        "AI" if audit_outcome.accepted else "DETERMINISTIC",
                    ),
                    "sources": result["ai_trace"].get("sources", []),
                    "tool_name": orchestration_tool_name,
                },
                dedupe_key=f"ai:{conversation.id}:{user_message.id}",
            )

        intent_classification = self._resolved_intent_classification(
            deterministic_intent=deterministic_intent,
            is_banking_operation=is_banking_operation,
            is_ticket_request=is_ticket_request,
            is_advisor_request=is_advisor_request,
            ticket_query_request=ticket_query_request,
            ticket_draft_flow=ticket_draft_flow,
            is_unhelpful_follow_up=is_unhelpful_follow_up,
            is_greeting=is_greeting,
            is_faq_discovery=is_faq_discovery,
            is_faq_category_request=is_faq_category_request,
            navigation_target=navigation_target,
            match=result.get("match"),
            intent=result.get("intent"),
        )
        result["intent_classification"] = intent_classification
        trace = result.get("ai_trace")
        if not isinstance(trace, dict):
            trace = {}
        trace.setdefault("intent_classification", intent_classification.value)
        trace.setdefault("response_source", result["response_source"])
        if result.get("fallback_reason") is not None:
            trace.setdefault("fallback_reason", result["fallback_reason"])
        result["ai_trace"] = trace

        conversation.turn_count += 1
        conversation.last_activity_at = datetime.now(UTC)
        conversation.last_confidence = result["confidence"]
        conversation.last_faq_id = result["faq_id"]
        if result["match"] is not None:
            conversation.category_id = result["match"].faq.category_id
        elif not result.get("preserve_context"):
            conversation.category_id = None
        conversation.detected_intent = result["intent"]
        conversation.pending_question = None
        if result["requires_clarification"]:
            conversation.pending_question = result.get("pending_question") or (
                "Selecciona la opción que describe mejor tu consulta."
                if result["options"]
                else "Cuéntame un poco más sobre lo que necesitas."
            )
        context = dict(conversation.context_json or {})
        if is_new_consultation:
            for key in _TRANSIENT_CONVERSATION_CONTEXT_KEYS:
                context.pop(key, None)
        if ticket_draft_reply is not None:
            if ticket_draft_reply.draft.status in {
                ChatbotTicketDraftStatus.COLLECTING,
                ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                ChatbotTicketDraftStatus.CONFIRMED,
                ChatbotTicketDraftStatus.CANCELLED,
            }:
                context[TICKET_DRAFT_CONTEXT_KEY] = self.ticket_drafts.persisted(
                    ticket_draft_reply.draft
                )
            else:
                context.pop(TICKET_DRAFT_CONTEXT_KEY, None)
        if result.get("advisor_handoff_requested") is True:
            context["advisor_handoff_requested"] = True
            if advisor_summary is not None:
                context["advisor_summary"] = advisor_summary
        elif ticket_draft_reply is not None and ticket_draft_reply.cancelled:
            context.pop("advisor_handoff_requested", None)
            context.pop("advisor_summary", None)
        pending_ticket_query = result.get("pending_ticket_query")
        if isinstance(pending_ticket_query, dict):
            context["pending_ticket_query"] = pending_ticket_query
        else:
            context.pop("pending_ticket_query", None)
        ticket_query_payload = result.get("ticket_query")
        if isinstance(ticket_query_payload, ChatbotTicketQuery):
            previous_codes = context.get("last_ticket_query_codes")
            stored_codes = (
                [code for code in previous_codes if isinstance(code, str)]
                if isinstance(previous_codes, list)
                else []
            )
            selected_code = (
                ticket_query_request.tracking_code
                if ticket_query_request is not None
                else None
            )
            if selected_code is None and isinstance(
                result.get("ticket_reference_codes"), list
            ):
                returned_codes = [
                    code
                    for code in result["ticket_reference_codes"]
                    if isinstance(code, str)
                ]
                if len(returned_codes) == 1:
                    selected_code = returned_codes[0]
            if (
                ticket_query_payload.query_type
                in {
                    ChatbotTicketQueryType.TICKET_STATUS,
                    ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS,
                    ChatbotTicketQueryType.ADVISOR_REPLY,
                }
                and selected_code in stored_codes
            ):
                context["last_ticket_query_codes"] = stored_codes
                context["last_ticket_query_index"] = stored_codes.index(selected_code)
            elif isinstance(result.get("ticket_reference_codes"), list):
                codes = [
                    code
                    for code in result["ticket_reference_codes"]
                    if isinstance(code, str)
                ]
                if codes:
                    context["last_ticket_query_codes"] = codes[:_MAX_CHAT_TICKETS]
                    context["last_ticket_query_index"] = None
                else:
                    context.pop("last_ticket_query_codes", None)
                    context.pop("last_ticket_query_index", None)
            elif ticket_query_payload.query_type in {
                ChatbotTicketQueryType.TICKET_LIST,
                ChatbotTicketQueryType.LATEST_TICKET,
            }:
                codes = [item.tracking_code for item in ticket_query_payload.tickets]
                if ticket_query_payload.ticket is not None:
                    codes.append(ticket_query_payload.ticket.tracking_code)
                if codes:
                    context["last_ticket_query_codes"] = codes
                    context["last_ticket_query_index"] = None
                else:
                    context.pop("last_ticket_query_codes", None)
                    context.pop("last_ticket_query_index", None)
        if ticket_draft_reply is not None:
            context.pop("last_ticket_query_codes", None)
            context.pop("last_ticket_query_index", None)
        if ticket_draft_reply is not None:
            context.pop("clarification_attempts", None)
            for key in (
                "pending_faq_ids",
                "pending_faq_category_ids",
                "pending_faq_category_id",
                "pending_faq_subcategories",
                "pending_faq_subcategory_id",
                "pending_ui_menu_id",
            ):
                context.pop(key, None)
            context.pop("clarification_context", None)
        else:
            if result.get("clear_clarification_attempts"):
                context.pop("clarification_attempts", None)
            elif result["requires_clarification"] and result.get(
                "counts_as_clarification", True
            ):
                context["clarification_attempts"] = (
                    int(context.get("clarification_attempts", 0)) + 1
                )
            elif result["resolved"]:
                context["clarification_attempts"] = 0
            if result["requires_clarification"]:
                intent = result.get("intent")
                if intent == "FAQ_CATEGORY_DISCOVERY":
                    context.pop("pending_faq_ids", None)
                    context["pending_faq_category_ids"] = result.get(
                        "candidate_faq_category_ids", []
                    )
                    for key in (
                        "pending_faq_category_id",
                        "pending_faq_subcategories",
                        "pending_faq_subcategory_id",
                    ):
                        context.pop(key, None)
                    context["pending_ui_menu_id"] = "faq_categories"
                elif intent == "FAQ_SUBCATEGORY_DISCOVERY":
                    context.pop("pending_faq_ids", None)
                    context.pop("pending_faq_category_ids", None)
                    context["pending_faq_category_id"] = result.get(
                        "candidate_faq_category_id"
                    )
                    context["pending_faq_subcategories"] = result.get(
                        "candidate_faq_subcategories", []
                    )
                    context.pop("pending_faq_subcategory_id", None)
                    context["pending_ui_menu_id"] = "faq_subcategories"
                elif result.get("candidate_faq_subcategory_id"):
                    context["pending_faq_ids"] = result.get("candidate_faq_ids", [])
                    context["pending_faq_subcategory_id"] = result.get(
                        "candidate_faq_subcategory_id"
                    )
                    context["pending_faq_category_id"] = result.get(
                        "candidate_faq_category_id"
                    )
                    context["pending_ui_menu_id"] = "faq_questions"
                elif result.get("candidate_faq_ids") and result.get("options"):
                    context["pending_faq_ids"] = result.get("candidate_faq_ids", [])
                    context.pop("pending_faq_category_ids", None)
                    context["pending_ui_menu_id"] = (
                        "faq_discovery"
                        if intent == "FAQ_DISCOVERY"
                        else "faq_suggestions"
                    )
                else:
                    context.pop("pending_faq_ids", None)
                    context.pop("pending_faq_category_ids", None)
                    context.pop("pending_ui_menu_id", None)
                if intent in {"FAQ_DISCOVERY", "FAQ_SUBCATEGORY_DISCOVERY"}:
                    context["clarification_context"] = []
                else:
                    previous = context.get("clarification_context", [])
                    context_seed = result.get("context_seed")
                    if isinstance(context_seed, str) and context_seed:
                        prior_context = [context_seed[:250]]
                    elif (
                        was_waiting_for_clarification
                        and self._is_contextual_follow_up(clean_content)
                        and isinstance(previous, list)
                    ):
                        prior_context = previous[-1:]
                    else:
                        prior_context = []
                    context["clarification_context"] = [
                        *prior_context,
                        clean_content[:250],
                    ][-2:]
            else:
                for key in (
                    "pending_faq_ids",
                    "pending_faq_category_ids",
                    "pending_faq_category_id",
                    "pending_faq_subcategories",
                    "pending_faq_subcategory_id",
                    "pending_ui_menu_id",
                ):
                    context.pop(key, None)
                context.pop("clarification_context", None)
        if not result.get("preserve_context"):
            context["last_terms"] = self._meaningful_tokens(search_content)[:20]
        if lifecycle_action is not None:
            for key in _TRANSIENT_CONVERSATION_CONTEXT_KEYS:
                context.pop(key, None)
            context["menu_open"] = lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
            if (
                lifecycle_action == ChatbotSuggestedAction.SHOW_MENU
                and current_draft.status
                in {
                    ChatbotTicketDraftStatus.COLLECTING,
                    ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                }
            ):
                context["ticket_draft_paused"] = True
            else:
                context.pop("ticket_draft_paused", None)
            conversation.category_id = None
            conversation.pending_question = None
        else:
            context["menu_open"] = is_greeting
            if active_draft and not start_ticket_draft and unrelated_special_message:
                context["ticket_draft_paused"] = True
            elif resume_paused_draft or start_ticket_draft:
                context.pop("ticket_draft_paused", None)
        conversation.context_json = context
        if lifecycle_action == ChatbotSuggestedAction.END_CONVERSATION:
            conversation.status = ConversationStatus.CLOSED
            conversation.ended_at = conversation.last_activity_at
            self.audit.record(
                session,
                event_type="CHATBOT",
                action="CONVERSATION_CLOSED_BY_CLIENT",
                actor_user_id=conversation.user_id,
                actor_role=current_user.role if current_user else None,
                resource_type="CONVERSATION",
                resource_id=conversation.id,
                target_user_id=conversation.user_id,
                success=True,
            )
        elif lifecycle_action == ChatbotSuggestedAction.SHOW_MENU:
            conversation.status = ConversationStatus.ACTIVE
            conversation.ended_at = None
        elif result["escalated"]:
            conversation.status = ConversationStatus.ESCALATED
            conversation.escalation_reason = result["fallback_reason"]
            conversation.escalated_at = conversation.last_activity_at
            self.audit.record(
                session,
                event_type="CHATBOT",
                action="CONVERSATION_ESCALATED",
                actor_user_id=conversation.user_id,
                actor_role=current_user.role if current_user else None,
                resource_type="CONVERSATION",
                resource_id=conversation.id,
                target_user_id=conversation.user_id,
                success=True,
                metadata={"reason": conversation.escalation_reason},
            )
        else:
            conversation.status = (
                ConversationStatus.WAITING_CLARIFICATION
                if result["requires_clarification"]
                else ConversationStatus.ACTIVE
            )
        session.add(conversation)

        bot_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.BOT,
                content=result["answer"],
                intent=result["intent"],
                confidence=result["confidence"],
                faq_id=result["faq_id"],
                response_source=result["response_source"],
                ai_trace_json=result["ai_trace"],
            ),
        )
        if result["escalated"] and conversation.user_id is not None:
            self.notifications.create(
                session,
                recipient_user_id=conversation.user_id,
                notification_type=NotificationType.CHAT_ESCALATED,
                title="Conversación derivada",
                message="Tu consulta requiere atención adicional.",
                related_conversation_id=conversation.id,
                metadata={"reason": result["fallback_reason"]},
                dedupe_key=f"chat_escalated:{conversation.id}:{conversation.turn_count}",
            )
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="MESSAGE_PROCESSED",
            actor_user_id=conversation.user_id,
            actor_role=current_user.role if current_user else None,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=conversation.user_id,
            success=True,
            metadata={
                "message_length": len(clean_content),
                "faq_id": result["faq_id"],
                "confidence": result["confidence"],
                "resolved": result["resolved"],
                "status": conversation.status.value,
            },
        )
        session.commit()
        session.refresh(conversation)
        draft = self._read_ticket_draft(session, conversation.context_json or {})
        ui = self._ui_for_result(session, result, conversation.context_json or {})
        return BotMessageResponse(
            user_message=self._message_read(user_message),
            bot_message=self._message_read(bot_message),
            resolved=result["resolved"],
            offers_ticket=result["offers_ticket"],
            confidence=result["confidence"],
            faq_id=result["faq_id"],
            source_category=result["category_name"],
            requires_clarification=result["requires_clarification"],
            clarification_options=result["options"] if ui is None else [],
            intent_classification=result.get("intent_classification"),
            ui=ui,
            suggested_action=result.get(
                "suggested_action", self._suggested_action(result)
            ),
            fallback_reason=result["fallback_reason"],
            conversation_status=conversation.status,
            response_source=result["response_source"],
            sources=result["sources"],
            ticket_query=result.get("ticket_query"),
            available_actions=result.get("available_actions", []),
            quick_replies=self._quick_replies(draft, result),
            proactive_notices=self._proactive_notices(session, current_user),
            ticket_draft=draft,
            menu_open=(conversation.context_json or {}).get("menu_open") is True,
        )

    def link_user(
        self,
        session: Session,
        conversation_id: str,
        current_user: AuthenticatedUser,
    ) -> dict[str, str]:
        conversation = self.conversations.get_conversation(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        if current_user.role != "CLIENTE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo un cliente puede asociar la conversación",
            )
        if conversation.user_id not in (None, current_user.user.id):
            raise HTTPException(status_code=403, detail="Conversación no autorizada")
        conversation.user_id = current_user.user.id
        conversation.anonymous_key = None
        session.add(conversation)
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="CONVERSATION_LINKED",
            actor_user_id=current_user.user.id,
            actor_role=current_user.role,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=current_user.user.id,
            success=True,
        )
        session.commit()
        session.refresh(conversation)
        return {
            "id": conversation.id,
            "user_id": conversation.user_id,
            "status": conversation.status.value,
        }

    def escalate(
        self,
        session: Session,
        conversation_id: str,
        reason: str | None,
        current_user: AuthenticatedUser | None,
    ) -> ConversationRead:
        self._require_public_actor(current_user)
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        if conversation.status == ConversationStatus.CONVERTED_TO_TICKET:
            raise HTTPException(
                status_code=409, detail="La conversación ya fue convertida"
            )
        if conversation.status == ConversationStatus.ESCALATED:
            return self._conversation_read(session, conversation, current_user)
        if (conversation.context_json or {}).get("advisor_handoff_requested") is True:
            return self._conversation_read(session, conversation, current_user)
        content = "Quiero hablar con un asesor"
        if reason:
            content += f". Motivo: {reason}"
        self.send_message(session, conversation_id, content, current_user)
        updated = self.conversations.get_conversation(session, conversation_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        return self._conversation_read(session, updated, current_user)

    def reset(
        self,
        session: Session,
        conversation_id: str,
        current_user: AuthenticatedUser | None,
    ) -> ConversationRead:
        self._require_public_actor(current_user)
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        if conversation.status == ConversationStatus.CONVERTED_TO_TICKET:
            raise HTTPException(
                status_code=409, detail="La conversación ya fue convertida"
            )
        if conversation.status == ConversationStatus.CLOSED:
            raise HTTPException(
                status_code=409, detail="La conversación ya está cerrada"
            )
        conversation.status = ConversationStatus.ACTIVE
        conversation.context_json = {}
        conversation.detected_intent = None
        conversation.last_faq_id = None
        conversation.category_id = None
        conversation.pending_question = None
        conversation.last_confidence = None
        conversation.escalation_reason = None
        conversation.escalated_at = None
        conversation.turn_count = 0
        conversation.last_activity_at = datetime.now(UTC)
        session.add(conversation)
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="CONVERSATION_RESET",
            actor_user_id=conversation.user_id,
            actor_role=current_user.role if current_user else None,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=conversation.user_id,
            success=True,
        )
        session.commit()
        session.refresh(conversation)
        return self._conversation_read(session, conversation, current_user)

    def feedback(
        self,
        session: Session,
        conversation_id: str,
        data: ChatFeedbackCreate,
        current_user: AuthenticatedUser | None,
    ) -> ChatFeedbackRead:
        self._require_public_actor(current_user)
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        bot_message = self.conversations.get_bot_message(
            session, conversation.id, data.message_id
        )
        if bot_message is None:
            raise HTTPException(
                status_code=409, detail="No hay una respuesta evaluable"
            )
        feedback = self.chat_feedback.get_by_message(session, bot_message.id)
        now = datetime.now(UTC)
        faq_feedback: FAQFeedback | None = None
        if feedback is None:
            if bot_message.faq_id is not None:
                faq_feedback = FAQFeedback(
                    faq_id=bot_message.faq_id,
                    user_id=current_user.user.id if current_user else None,
                    is_helpful=data.is_helpful,
                    escalation_accepted=data.escalation_accepted,
                    comment=data.reason,
                )
                session.add(faq_feedback)
                session.flush()
            feedback = ChatbotFeedback(
                conversation_id=conversation.id,
                message_id=bot_message.id,
                faq_id=bot_message.faq_id,
                faq_feedback_id=faq_feedback.id if faq_feedback else None,
                user_id=current_user.user.id if current_user else None,
                is_helpful=data.is_helpful,
                escalation_accepted=data.escalation_accepted,
                created_at=now,
                updated_at=now,
            )
            self.chat_feedback.add(session, feedback)
        else:
            feedback.is_helpful = data.is_helpful
            feedback.escalation_accepted = data.escalation_accepted
            feedback.updated_at = now
            if feedback.faq_feedback_id:
                faq_feedback = session.get(FAQFeedback, feedback.faq_feedback_id)
                if faq_feedback is not None:
                    faq_feedback.is_helpful = data.is_helpful
                    faq_feedback.escalation_accepted = data.escalation_accepted
                    faq_feedback.comment = data.reason
                    session.add(faq_feedback)
            session.add(feedback)
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="FEEDBACK_RECEIVED",
            actor_user_id=current_user.user.id if current_user else None,
            actor_role=current_user.role if current_user else None,
            resource_type="CHAT_MESSAGE",
            resource_id=bot_message.id,
            target_user_id=conversation.user_id,
            success=True,
            metadata={
                "conversation_id": conversation.id,
                "faq_id": bot_message.faq_id,
                "is_helpful": data.is_helpful,
                "escalation_accepted": data.escalation_accepted,
            },
        )
        session.commit()
        return ChatFeedbackRead(
            id=feedback.id,
            message_id=feedback.message_id,
            is_helpful=feedback.is_helpful,
            escalation_accepted=feedback.escalation_accepted,
            created_at=feedback.created_at,
        )

    def feedback_metrics(
        self, session: Session, actor: AuthenticatedUser
    ) -> ChatbotFeedbackMetrics:
        if actor.role != "SUPERVISOR":
            raise HTTPException(status_code=403, detail="Métricas no autorizadas")
        total, helpful, faq_backed = self.chat_feedback.metrics(session)
        return ChatbotFeedbackMetrics(
            total_feedback=total,
            helpful=helpful,
            not_helpful=total - helpful,
            usefulness_rate=round(helpful / total * 100, 2) if total else 0.0,
            faq_backed=faq_backed,
            generic_replies=total - faq_backed,
        )

    def operational_metrics(
        self, session: Session, actor: AuthenticatedUser
    ) -> ChatbotOperationalMetrics:
        if actor.role != "SUPERVISOR":
            raise HTTPException(status_code=403, detail="Métricas no autorizadas")
        settings = get_settings()
        if not settings.chatbot_aggregate_metrics_enabled:
            raise HTTPException(status_code=404, detail="Métricas no disponibles")

        messages = list(
            session.exec(
                select(ChatMessage).where(ChatMessage.sender_type == SenderType.BOT)
            ).all()
        )
        conversations = list(session.exec(select(Conversation)).all())
        chatbot_tickets = self.tickets.repository.list_tickets(
            session, source=TicketSource.CHATBOT
        )
        fallback_by_reason: dict[str, int] = {}
        out_of_context_by_subrule: dict[str, int] = {}
        intent_counts: dict[str, int] = {}
        fallback_count = 0
        provider_calls_today = 0
        tokens_today = 0
        estimated_cost_today = 0.0
        today = datetime.now(UTC).date()

        for message in messages:
            trace = (
                message.ai_trace_json if isinstance(message.ai_trace_json, dict) else {}
            )
            reason = trace.get("fallback_reason")
            if isinstance(reason, str) and reason:
                fallback_by_reason[reason] = fallback_by_reason.get(reason, 0) + 1
                fallback_count += 1
            elif message.response_source == "FALLBACK":
                fallback_by_reason["UNSPECIFIED"] = (
                    fallback_by_reason.get("UNSPECIFIED", 0) + 1
                )
                fallback_count += 1

            intent = trace.get("intent_classification") or message.intent or "UNKNOWN"
            if isinstance(intent, str):
                intent_counts[intent] = intent_counts.get(intent, 0) + 1
            if reason == "OUT_OF_CONTEXT" or intent == ChatbotIntent.OUT_OF_SCOPE.value:
                subrule = trace.get("validation_subrule")
                subrule = (
                    subrule
                    if isinstance(subrule, str) and subrule
                    else (
                        reason
                        if isinstance(reason, str) and reason
                        else "OUT_OF_CONTEXT"
                    )
                )
                out_of_context_by_subrule[subrule] = (
                    out_of_context_by_subrule.get(subrule, 0) + 1
                )

            created_at = message.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at.date() != today:
                continue
            if trace.get("provider_called") is True:
                call_count = trace.get("provider_call_count")
                provider_calls_today += (
                    max(call_count, 0)
                    if isinstance(call_count, int) and not isinstance(call_count, bool)
                    else 1
                )
            input_tokens = trace.get("input_tokens")
            output_tokens = trace.get("output_tokens")
            total_tokens = trace.get("total_tokens")
            if isinstance(total_tokens, int) and total_tokens >= 0:
                tokens_today += total_tokens
            else:
                tokens_today += sum(
                    value
                    for value in (input_tokens, output_tokens)
                    if isinstance(value, int) and value >= 0
                )
            cost = trace.get("estimated_cost_usd")
            if (
                isinstance(cost, int | float)
                and not isinstance(cost, bool)
                and cost >= 0
            ):
                estimated_cost_today += float(cost)

        total_messages = len(messages)
        fallback_rates = {
            reason: round(count / total_messages * 100, 2)
            for reason, count in fallback_by_reason.items()
        }
        advisor_handoffs = sum(
            1
            for conversation in conversations
            if conversation.escalated_at is not None
            or (conversation.context_json or {}).get("advisor_handoff_requested")
            is True
        )
        total_conversations = len(conversations)
        return ChatbotOperationalMetrics(
            total_conversations=total_conversations,
            conversations_ending_in_ticket=sum(
                item.status == ConversationStatus.CONVERTED_TO_TICKET
                for item in conversations
            ),
            tickets_created=len(chatbot_tickets),
            escalation_requests=advisor_handoffs,
            escalation_rate_percent=(
                round(advisor_handoffs / total_conversations * 100, 2)
                if total_conversations
                else 0.0
            ),
            fallback_by_reason=fallback_by_reason,
            fallback_rate_by_reason_percent=fallback_rates,
            out_of_context_by_subrule=out_of_context_by_subrule,
            intent_counts=intent_counts,
            provider_calls_today=provider_calls_today,
            tokens_today=tokens_today,
            estimated_cost_today_usd=round(estimated_cost_today, 8),
            total_call_cap=settings.chatbot_ai_total_max_calls,
            total_token_cap=settings.chatbot_ai_total_max_tokens,
            total_estimated_cost_cap_usd=settings.chatbot_ai_total_estimated_cost_usd,
            max_output_tokens_per_call=settings.chatbot_ai_max_output_tokens,
        )

    def _find_matches(self, session: Session, content: str) -> list[FAQMatch]:
        query = self.normalize_text(content)
        query_terms = self._meaningful_tokens(query)
        query_forms = self._token_forms(" ".join(query_terms))
        # Avoid treating one shared product word (for example, “tarjeta”) as
        # evidence that a FAQ answers an otherwise unrelated question.
        if len(query_forms) < 2:
            return []
        matches: list[FAQMatch] = []
        for faq in self.faqs.list_active(session):
            if not self._is_publicly_available_faq(faq):
                continue
            question = self.normalize_text(faq.question)
            title = self.normalize_text(faq.title)
            fields = [
                question,
                title,
                self.normalize_text(faq.summary),
                self.normalize_text(faq.answer),
            ]
            if detect_prompt_injection(" ".join(fields)):
                continue
            field_tokens = self._token_forms(" ".join(fields))
            keyword_tokens = self._token_forms(
                " ".join(
                    [
                        *self._keywords(faq.keywords),
                        *self._json_terms(faq.tags),
                        *self._json_terms(faq.synonyms),
                    ]
                )
            )
            overlap = len(query_forms & field_tokens) / len(query_forms)
            keyword_overlap = len(query_forms & keyword_tokens) / len(query_forms)
            score = min(1.0, 0.55 * overlap + 0.45 * keyword_overlap)
            candidate_tokens = field_tokens | keyword_tokens
            fuzzy_overlap = (
                self._fuzzy_overlap(query_forms, candidate_tokens)
                if query_forms & candidate_tokens
                else 0.0
            )
            score = max(
                score,
                0.80 * keyword_overlap,
                0.75 * fuzzy_overlap,
            )
            if query in question or query in title:
                score = 1.0
            if score > 0:
                matches.append(
                    FAQMatch(
                        faq=faq,
                        score=round(score, 4),
                        intent=self._intent_for(faq),
                        category_name=self._category_name(session, faq.category_id),
                    )
                )
        return sorted(
            matches,
            key=lambda item: (
                item.score,
                item.faq.priority,
                -item.faq.display_order,
                item.faq.id,
            ),
            reverse=True,
        )

    def _retrieval_candidates(
        self, session: Session, lexical_matches: list[FAQMatch]
    ) -> list[FAQMatch]:
        lexical_by_id = {match.faq.id: match for match in lexical_matches}
        candidates: list[FAQMatch] = []
        for faq in self._available_public_faqs(session):
            lexical = lexical_by_id.get(faq.id)
            candidates.append(
                FAQMatch(
                    faq=faq,
                    score=lexical.score if lexical is not None else 0.0,
                    intent=(
                        lexical.intent if lexical is not None else self._intent_for(faq)
                    ),
                    category_name=(
                        lexical.category_name if lexical is not None else None
                    ),
                )
            )
        return candidates

    def _response_for_matches(
        self, conversation: Conversation, matches: list[FAQMatch]
    ) -> dict[str, object]:
        settings = get_settings()
        best = matches[0] if matches else None
        confidence = best.score if best else 0.0
        attempts = int(
            (conversation.context_json or {}).get("clarification_attempts", 0)
        )
        ambiguous = bool(
            best and len(matches) > 1 and best.score - matches[1].score < 0.15
        )
        needs_clarification = bool(
            best
            and confidence >= settings.chatbot_medium_confidence_threshold
            and (confidence < settings.chatbot_high_confidence_threshold or ambiguous)
        )
        if (
            needs_clarification
            and attempts < settings.chatbot_max_clarification_attempts
        ):
            options = [item.faq.title or item.faq.question for item in matches[:3]]
            return {
                "answer": "Necesito un poco más de detalle para orientarte. "
                "¿Cuál de estas opciones describe mejor tu consulta?",
                "intent": best.intent,
                "faq_id": best.faq.id,
                "category_name": best.category_name,
                "confidence": confidence,
                "resolved": False,
                "offers_ticket": False,
                "requires_clarification": True,
                "options": options,
                "candidate_faq_ids": [item.faq.id for item in matches[:3]],
                "fallback_reason": "MEDIUM_CONFIDENCE",
                "escalated": False,
                "match": best,
                "response_source": "DETERMINISTIC",
            }
        if best and confidence >= settings.chatbot_high_confidence_threshold:
            return {
                "answer": best.faq.answer,
                "intent": best.intent,
                "faq_id": best.faq.id,
                "category_name": best.category_name,
                "confidence": confidence,
                "resolved": True,
                "offers_ticket": False,
                "requires_clarification": False,
                "options": [],
                "candidate_faq_ids": [],
                "fallback_reason": None,
                "escalated": False,
                "match": best,
                "response_source": "DETERMINISTIC",
            }
        if attempts < settings.chatbot_max_clarification_attempts:
            return {
                "answer": (
                    "Para orientarte mejor, ¿puedes contarme un poco más sobre "
                    "lo que necesitas o el inconveniente que tienes?"
                ),
                "intent": None,
                "faq_id": None,
                "category_name": None,
                "confidence": confidence,
                "resolved": False,
                "offers_ticket": False,
                "requires_clarification": True,
                "options": [],
                "candidate_faq_ids": [],
                "fallback_reason": None,
                "escalated": False,
                "match": None,
                "response_source": "DETERMINISTIC",
            }
        return {
            "answer": (
                "Aún no logro identificar tu consulta. Puedes reformularla o, "
                "si lo prefieres, solicitar atención humana o convertir la "
                "conversación en un ticket."
            ),
            "intent": best.intent if best else None,
            "faq_id": best.faq.id if best else None,
            "category_name": best.category_name if best else None,
            "confidence": confidence,
            "resolved": False,
            "offers_ticket": True,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": "LOW_CONFIDENCE",
            "escalated": False,
            "match": best,
            "response_source": "FALLBACK",
        }

    def _available_public_faqs(self, session: Session) -> list[FAQ]:
        return [
            faq
            for faq in self.faqs.list_active(session)
            if self._is_publicly_available_faq(faq)
            and not detect_prompt_injection(
                f"{faq.title} {faq.question} {faq.summary} {faq.answer}"
            )
        ]

    @staticmethod
    def _is_publicly_available_faq(faq: FAQ) -> bool:
        return has_verified_official_source(
            faq.source_url, faq.source_verified_at
        ) or is_safe_unverified_faq(faq)

    def _available_faq_categories(self, session: Session) -> list[tuple[str, str]]:
        categories: dict[str, str] = {}
        for faq in self._available_public_faqs(session):
            name = self._category_name(session, faq.category_id)
            if name:
                categories.setdefault(faq.category_id, name)
        return sorted(categories.items(), key=lambda category: category[1].casefold())

    def _faq_category_discovery_response(self, session: Session) -> dict[str, object]:
        categories = self._available_faq_categories(session)[:19]
        labels = [name for _, name in categories]
        return {
            "answer": (
                "Elige una categoría para ver preguntas frecuentes publicadas."
                if categories
                else "No hay preguntas frecuentes publicadas disponibles ahora."
            ),
            "intent": "FAQ_CATEGORY_DISCOVERY",
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": not categories,
            "offers_ticket": False,
            "requires_clarification": bool(categories),
            "options": labels,
            "candidate_faq_ids": [],
            "candidate_faq_category_ids": [
                category_id for category_id, _ in categories
            ],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "sources": [],
            "counts_as_clarification": False,
        }

    def _faq_subcategory_discovery_response(
        self, session: Session, *, category_id: str
    ) -> tuple[dict[str, object], list[FAQMatch]]:
        category_name = self._category_name(session, category_id)
        faqs = [
            faq
            for faq in self._available_public_faqs(session)
            if faq.category_id == category_id
        ]
        if category_name is None or not faqs:
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )

        grouped: dict[str, dict[str, object]] = {}
        for faq in faqs:
            label = self._faq_subcategory_label(faq, category_name)
            group_key = normalize_text(label)
            group = grouped.setdefault(group_key, {"label": label, "faq_ids": []})
            faq_ids = group["faq_ids"]
            if isinstance(faq_ids, list):
                faq_ids.append(faq.id)

        # A single topic adds no useful navigation step, so retain the existing
        # category -> FAQ flow for catalogs without multiple verified topics.
        if len(grouped) < 2:
            return self._faq_discovery_response(session, category_id=category_id)

        subcategories: list[dict[str, object]] = []
        for key, group in sorted(
            grouped.items(), key=lambda item: str(item[1]["label"]).casefold()
        )[:18]:
            identity = hashlib.sha256(f"{category_id}:{key}".encode()).hexdigest()[:16]
            subcategories.append(
                {
                    "id": f"subcategory:{identity}",
                    "label": group["label"],
                    "category_id": category_id,
                    "faq_ids": group["faq_ids"],
                }
            )

        matches = [
            FAQMatch(
                faq=faq,
                score=1.0,
                intent=self._intent_for(faq),
                category_name=category_name,
            )
            for faq in faqs
        ]
        labels = [str(item["label"]) for item in subcategories]
        return (
            {
                "answer": (
                    f"Elige un tema de {category_name} para ver preguntas frecuentes."
                ),
                "intent": "FAQ_SUBCATEGORY_DISCOVERY",
                "faq_id": None,
                "category_name": category_name,
                "confidence": 1.0,
                "resolved": False,
                "offers_ticket": False,
                "requires_clarification": True,
                "options": labels,
                "candidate_faq_ids": [],
                "candidate_faq_category_id": category_id,
                "candidate_faq_subcategories": subcategories,
                "fallback_reason": None,
                "escalated": False,
                "match": None,
                "response_source": "DETERMINISTIC",
                "sources": self._public_sources(matches[:_FAQ_DISCOVERY_MAX_OPTIONS]),
                "counts_as_clarification": False,
            },
            matches,
        )

    @staticmethod
    def _faq_subcategory_label(faq: FAQ, category_name: str) -> str:
        title = faq.title or faq.question
        title_words = re.findall(r"[^\W_]+", title, re.UNICODE)
        normalized_words = [normalize_text(word) for word in title_words]
        category_words = set(normalize_text(category_name.replace("_", " ")).split())
        category_words.update(
            word[:-1]
            for word in tuple(category_words)
            if len(word) > 3 and word.endswith("s")
        )
        category_words.update(_FAQ_SUBCATEGORY_TAG_STOP_WORDS)
        tag_words = {
            word
            for raw_tag in ChatbotService._json_terms(faq.tags)
            for word in normalize_text(raw_tag).split()
        }
        useful_positions = [
            index
            for index, word in enumerate(normalized_words)
            if word in tag_words
            and word not in category_words
            and word not in _FAQ_SUBCATEGORY_STOP_WORDS
        ]

        groups: list[list[int]] = []
        for position in useful_positions:
            if not groups or position - groups[-1][-1] > 2:
                groups.append([position])
            else:
                groups[-1].append(position)
        if groups:
            selected = max(
                groups, key=lambda positions: (len(positions), -positions[0])
            )
            label_words = title_words[selected[0] : selected[-1] + 1]
            label = " ".join(label_words)
        else:
            tag_words_in_order = [
                word
                for raw_tag in ChatbotService._json_terms(faq.tags)
                for word in re.findall(r"[^\W_]+", raw_tag, re.UNICODE)
                if normalize_text(word) not in category_words
                and normalize_text(word) not in _FAQ_SUBCATEGORY_STOP_WORDS
            ]
            label = " ".join(tag_words_in_order[:3]) or "Otros temas"

        label = label.strip(" .,!?:;¿¡")[:60]
        return label[:1].upper() + label[1:] if label else "Otros temas"

    def _faq_discovery_response(
        self,
        session: Session,
        *,
        category_id: str | None = None,
        faq_ids: set[str] | None = None,
    ) -> tuple[dict[str, object], list[FAQMatch]]:
        available_faqs = self._available_public_faqs(session)
        if category_id is not None:
            available_faqs = [
                faq for faq in available_faqs if faq.category_id == category_id
            ]
        if faq_ids is not None:
            available_faqs = [faq for faq in available_faqs if faq.id in faq_ids]
        selected: list[FAQMatch] = []
        seen_categories: set[str] = set()

        # Keep a stable, broad menu rather than showing five questions from one topic.
        category_names = {
            faq.category_id: self._category_name(session, faq.category_id)
            for faq in available_faqs
        }
        for category_name in _FAQ_DISCOVERY_CATEGORY_ORDER:
            faq = next(
                (
                    candidate
                    for candidate in available_faqs
                    if category_names.get(candidate.category_id) == category_name
                ),
                None,
            )
            if faq is None:
                continue
            seen_categories.add(faq.category_id)
            selected.append(
                FAQMatch(
                    faq=faq,
                    score=1.0,
                    intent=self._intent_for(faq),
                    category_name=category_names[faq.category_id],
                )
            )
            if len(selected) == _FAQ_DISCOVERY_MAX_OPTIONS:
                break

        # Include published FAQ categories not in the preferred menu if space remains.
        if len(selected) < _FAQ_DISCOVERY_MAX_OPTIONS:
            for faq in available_faqs:
                if faq.category_id in seen_categories:
                    continue
                seen_categories.add(faq.category_id)
                selected.append(
                    FAQMatch(
                        faq=faq,
                        score=1.0,
                        intent=self._intent_for(faq),
                        category_name=category_names.get(faq.category_id),
                    )
                )
                if len(selected) == _FAQ_DISCOVERY_MAX_OPTIONS:
                    break

        if len(selected) < _FAQ_DISCOVERY_MAX_OPTIONS:
            selected_ids = {match.faq.id for match in selected}
            for faq in available_faqs:
                if faq.id in selected_ids:
                    continue
                selected.append(
                    FAQMatch(
                        faq=faq,
                        score=1.0,
                        intent=self._intent_for(faq),
                        category_name=self._category_name(session, faq.category_id),
                    )
                )
                if len(selected) == _FAQ_DISCOVERY_MAX_OPTIONS:
                    break

        options = [match.faq.title or match.faq.question for match in selected]
        categories = list(
            dict.fromkeys(
                match.category_name for match in selected if match.category_name
            )
        )
        if options:
            category_text = ", ".join(categories)
            answer = (
                f"Puedo orientarte sobre {category_text}. Elige una de estas "
                "preguntas frecuentes o cuéntame con tus palabras qué necesitas."
            )
        else:
            answer = (
                "En este momento no hay preguntas frecuentes publicadas para "
                "mostrarte. Cuéntame qué necesitas y buscaré la mejor forma de "
                "orientarte."
            )

        return (
            {
                "answer": answer,
                "intent": "FAQ_DISCOVERY",
                "faq_id": None,
                "category_name": None,
                "confidence": 1.0,
                "resolved": False,
                "offers_ticket": False,
                "requires_clarification": True,
                "options": options,
                "candidate_faq_ids": [match.faq.id for match in selected],
                "fallback_reason": None,
                "escalated": False,
                "match": None,
                "response_source": "DETERMINISTIC",
                "sources": self._public_sources(selected),
                "counts_as_clarification": False,
            },
            selected,
        )

    @staticmethod
    def _is_pure_greeting(content: str) -> bool:
        normalized = normalize_text(content)
        normalized = re.sub(r"^[^\w]+|[^\w]+$", "", normalized).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized in _GREETING_PHRASES

    @staticmethod
    def _is_faq_discovery_request(content: str) -> bool:
        normalized = normalize_text(content).strip(" .!?¿¡")
        if normalized in {"faq", "faqs", "ayuda", "temas de ayuda"}:
            return True
        return any(phrase in normalized for phrase in _FAQ_DISCOVERY_PHRASES)

    @staticmethod
    def _is_faq_category_discovery_request(content: str) -> bool:
        normalized = normalize_text(content).strip(" .!?¿¡")
        return any(phrase in normalized for phrase in _FAQ_CATEGORY_DISCOVERY_PHRASES)

    @staticmethod
    def _is_new_consultation_request(content: str) -> bool:
        normalized = normalize_text(content).strip(" .!?¿¡")
        return any(phrase in normalized for phrase in _NEW_CONSULTATION_PHRASES)

    @staticmethod
    def _is_clear_faq_question(content: str) -> bool:
        normalized = normalize_text(content).strip(" .!?¿¡")
        asks_for_information = bool(
            re.search(
                r"^(?:que|como|cuando|donde|quien|cual|cuanto|"
                r"requisitos?|documentos?|plazos?|horarios?|costos?|"
                r"beneficios?|canales?)\b",
                normalized,
            )
        )
        return asks_for_information and bool(_BANKING_TOPIC_PATTERN.search(normalized))

    def _requested_faq_category_id(self, session: Session, content: str) -> str | None:
        normalized = normalize_text(content)
        if re.search(r"\b(?:categoria|category)\s*[:=]", normalized):
            return None
        if not any(
            re.search(rf"\b{re.escape(term)}\b", normalized)
            for term in _CATEGORY_HELP_TERMS
        ):
            return None

        category_matches: list[tuple[int, str]] = []
        for category_id, label in self._available_faq_categories(session):
            words = normalize_text(label.replace("_", " ")).split()
            if not words:
                continue
            patterns = []
            for word in words:
                singular = word[:-1] if len(word) > 4 and word.endswith("s") else word
                patterns.append(
                    rf"\b(?:{re.escape(singular)}|{re.escape(singular + 's')})\b"
                )
            if all(re.search(pattern, normalized) for pattern in patterns):
                category_matches.append((len(words), category_id))
        if not category_matches:
            return None
        category_matches.sort(reverse=True)
        return category_matches[0][1]

    def _resolve_pending_selection(
        self, session: Session, conversation: Conversation, content: str
    ) -> FAQMatch | None:
        raw_ids = (conversation.context_json or {}).get("pending_faq_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            return None
        active = {
            faq.id: faq
            for faq in self.faqs.list_active(session)
            if faq.id in {value for value in raw_ids if isinstance(value, str)}
            and self._is_publicly_available_faq(faq)
        }
        if not active:
            return None
        candidates = [
            active[faq_id]
            for faq_id in raw_ids
            if isinstance(faq_id, str) and faq_id in active
        ]
        normalized = self.normalize_text(content).strip(" .!?¿¡")
        ordinal = re.fullmatch(
            r"(?:(?:la|el|opcion|pregunta)\s+)*(primera|primer|1|uno|una|segunda|segundo|2|dos|tercera|tercer|3|tres|cuarta|cuarto|4|cuatro)(?:\s+(?:opcion|pregunta))?",
            normalized,
        )
        if ordinal:
            ordinal_index = {
                "primera": 0,
                "primer": 0,
                "1": 0,
                "uno": 0,
                "una": 0,
                "segunda": 1,
                "segundo": 1,
                "2": 1,
                "dos": 1,
                "tercera": 2,
                "tercer": 2,
                "3": 2,
                "tres": 2,
                "cuarta": 3,
                "cuarto": 3,
                "4": 3,
                "cuatro": 3,
            }[ordinal.group(1)]
            if ordinal_index < len(candidates):
                selected = candidates[ordinal_index]
                return FAQMatch(
                    faq=selected,
                    score=1.0,
                    intent=self._intent_for(selected),
                    category_name=self._category_name(session, selected.category_id),
                )
        for faq in candidates:
            labels = {self.normalize_text(faq.title), self.normalize_text(faq.question)}
            if normalized in labels:
                return FAQMatch(
                    faq=faq,
                    score=1.0,
                    intent=self._intent_for(faq),
                    category_name=self._category_name(session, faq.category_id),
                )
        return None

    @classmethod
    def _is_contextual_follow_up(cls, content: str) -> bool:
        normalized = cls.normalize_text(content)
        terms = cls._meaningful_tokens(normalized)
        referential_terms = {
            "eso",
            "esa",
            "ese",
            "tambien",
            "ademas",
            "entonces",
            "mas",
            "otro",
            "otra",
            "donde",
            "cuando",
            "cual",
        }
        return len(terms) <= 4 or bool(set(terms) & referential_terms)

    @classmethod
    def _is_referential_follow_up(cls, content: str) -> bool:
        normalized = cls.normalize_text(content).strip()
        terms = set(cls._meaningful_tokens(normalized))
        referential_terms = {
            "eso",
            "esa",
            "ese",
            "esto",
            "estos",
            "estas",
            "aquello",
            "anterior",
            "tambien",
            "ademas",
            "entonces",
            "otro",
            "otra",
        }
        if terms & referential_terms:
            return True
        return bool(
            re.search(
                r"\b(?:y|tambien|ademas|entonces)\b.{0,35}\b"
                r"(?:lo|la|ahi|alli|asi)\b",
                normalized,
            )
        )

    @classmethod
    def _is_ticket_request(cls, content: str) -> bool:
        return bool(_TICKET_REQUEST_PATTERN.search(cls.normalize_text(content)))

    @classmethod
    def _classify_deterministic_intent(cls, content: str) -> ChatbotIntent:
        if cls._is_banking_operation_request(content):
            return ChatbotIntent.OUT_OF_SCOPE
        if cls._ticket_query_request(content) is not None:
            return ChatbotIntent.TICKET_QUERY
        if cls._is_ticket_request(content):
            return ChatbotIntent.CREATE_TICKET
        if cls._is_advisor_request(content):
            return ChatbotIntent.REQUEST_ADVISOR
        if cls._is_feedback_request(content):
            return ChatbotIntent.FEEDBACK
        if cls._is_pure_greeting(content):
            return ChatbotIntent.GREETING
        if cls._is_faq_discovery_request(content):
            return ChatbotIntent.FAQ_DISCOVERY
        if cls._is_obviously_out_of_scope(content):
            return ChatbotIntent.OUT_OF_SCOPE
        if "?" in content or re.search(
            r"\b(?:que|como|cuando|donde|quien|cual|cuanto|cuanta)\b",
            normalize_text(content),
        ):
            return ChatbotIntent.FAQ_QUERY
        return ChatbotIntent.UNKNOWN

    @staticmethod
    def _is_banking_operation_request(content: str) -> bool:
        normalized = normalize_text(content)
        if not _BANKING_OPERATION_PATTERN.search(normalized):
            return False
        if _TICKET_REQUEST_PATTERN.search(normalized) or any(
            pattern.search(normalized) for pattern in _ADVISOR_REQUEST_PATTERNS
        ):
            return False
        return not re.search(
            r"\b(?:categor[ií]a|category|asunto|t[ií]tulo|subject|"
            r"descripci[oó]n|detalle|description|prioridad|priority)\b\s*[:=]",
            content,
            re.IGNORECASE,
        )

    @staticmethod
    def _banking_operation_response() -> dict[str, object]:
        return {
            "answer": (
                "Este canal no realiza operaciones bancarias ni consulta saldos, "
                "transferencias o movimientos. Puedo mostrarte preguntas frecuentes "
                "o ayudarte a crear un ticket de soporte."
            ),
            "intent": ChatbotIntent.OUT_OF_SCOPE.value,
            "intent_classification": ChatbotIntent.OUT_OF_SCOPE,
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": True,
            "offers_ticket": True,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": "BANKING_OPERATION_OUT_OF_SCOPE",
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "sources": [],
            "ai_trace": {
                "fallback": True,
                "fallback_reason": "BANKING_OPERATION_OUT_OF_SCOPE",
                "validation_subrule": "BANKING_OPERATION_OUT_OF_SCOPE",
                "response_source": "DETERMINISTIC",
            },
            "suggested_action": ChatbotSuggestedAction.SHOW_MENU,
            "available_actions": [
                ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                ChatbotAvailableAction.CREATE_TICKET,
            ],
        }

    def _out_of_scope_response(self, session: Session) -> dict[str, object]:
        category_response = self._faq_category_discovery_response(session)
        return {
            "answer": (
                "Entiendo tu pregunta. Este asistente se especializa en orientación "
                "bancaria y seguimiento de solicitudes. Puedo mostrarte las "
                "categorías disponibles."
            ),
            "intent": ChatbotIntent.OUT_OF_SCOPE.value,
            "intent_classification": ChatbotIntent.OUT_OF_SCOPE,
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": False,
            "offers_ticket": False,
            "requires_clarification": bool(category_response["options"]),
            "options": category_response["options"],
            "candidate_faq_ids": [],
            "candidate_faq_category_ids": category_response[
                "candidate_faq_category_ids"
            ],
            "fallback_reason": "OUT_OF_SCOPE",
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "sources": [],
            "ai_trace": {
                "fallback": True,
                "fallback_reason": "OUT_OF_SCOPE",
                "validation_subrule": "OUT_OF_SCOPE_REDIRECT_REQUIRED",
                "response_source": "DETERMINISTIC",
            },
            "suggested_action": ChatbotSuggestedAction.ASK_CLARIFICATION,
            "available_actions": [
                ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                ChatbotAvailableAction.CREATE_TICKET,
                ChatbotAvailableAction.CONTACT_ADVISOR,
            ],
            "counts_as_clarification": False,
        }

    @staticmethod
    def _conversational_intent_for_result(
        result: dict[str, object],
        *,
        lifecycle_action: ChatbotSuggestedAction | None,
        is_greeting: bool,
        is_out_of_scope_request: bool,
    ) -> ChatbotIntent:
        if is_out_of_scope_request:
            return ChatbotIntent.OUT_OF_SCOPE
        if is_greeting:
            return ChatbotIntent.GREETING
        if lifecycle_action == ChatbotSuggestedAction.SHOW_MENU:
            return ChatbotIntent.SHOW_MENU
        intent = result.get("intent")
        if intent == "FOLLOW_UP_UNRESOLVED":
            return ChatbotIntent.CLARIFICATION
        try:
            classification = ChatbotIntent(str(intent))
        except ValueError:
            return ChatbotIntent.CLARIFICATION
        if classification in {
            ChatbotIntent.CLARIFICATION,
            ChatbotIntent.FEEDBACK,
            ChatbotIntent.FAQ_DISCOVERY,
            ChatbotIntent.FAQ_CATEGORY_DISCOVERY,
            ChatbotIntent.FAQ_SUBCATEGORY_DISCOVERY,
            ChatbotIntent.NAVIGATION,
            ChatbotIntent.OUT_OF_SCOPE,
            ChatbotIntent.UNKNOWN,
        }:
            return classification
        return ChatbotIntent.CLARIFICATION

    @staticmethod
    def _is_public_context_message(message: ChatMessage) -> bool:
        private_or_ticket_intents = {
            "AUTHENTICATION_REQUIRED",
            "TICKET_QUERY",
            "TICKET_LIST",
            "TICKET_COUNT",
            "TICKET_STATUS",
            "TICKET_DETAIL",
            "ADVISOR_RESPONSE_STATUS",
            "LATEST_ADVISOR_RESPONSE",
            "CLIENT_ACTION_REQUIRED",
            "TICKET_DRAFT_UPDATE",
            "TICKET_CREATION_AUTHENTICATION_REQUIRED",
            "ADVISOR_REQUEST_AUTHENTICATION_REQUIRED",
        }
        return not ChatbotService._is_ticket_query_intent(message.intent) and (
            message.intent not in private_or_ticket_intents
        )

    @classmethod
    def _is_feedback_request(cls, content: str) -> bool:
        normalized = cls.normalize_text(content).strip(" .,!?:;¿¡")
        return normalized == "feedback" or bool(
            _UNHELPFUL_FOLLOW_UP_PATTERN.search(normalized)
        )

    @staticmethod
    def _resolved_intent_classification(
        *,
        deterministic_intent: ChatbotIntent,
        is_banking_operation: bool,
        is_ticket_request: bool,
        is_advisor_request: bool,
        ticket_query_request: TicketQueryRequest | None,
        ticket_draft_flow: bool,
        is_unhelpful_follow_up: bool,
        is_greeting: bool,
        is_faq_discovery: bool,
        is_faq_category_request: bool,
        navigation_target: str | None,
        match: FAQMatch | None,
        intent: object,
    ) -> ChatbotIntent:
        if is_banking_operation or intent == ChatbotIntent.OUT_OF_SCOPE.value:
            return ChatbotIntent.OUT_OF_SCOPE
        if ticket_query_request is not None:
            return ChatbotIntent.TICKET_QUERY
        if is_ticket_request:
            return ChatbotIntent.CREATE_TICKET
        if is_advisor_request:
            return ChatbotIntent.REQUEST_ADVISOR
        if ticket_draft_flow:
            return ChatbotIntent.TICKET_DRAFT_UPDATE
        if deterministic_intent == ChatbotIntent.FEEDBACK:
            return ChatbotIntent.FEEDBACK
        if is_unhelpful_follow_up:
            return ChatbotIntent.FEEDBACK
        if is_greeting:
            return ChatbotIntent.GREETING
        if is_faq_discovery:
            return ChatbotIntent.FAQ_DISCOVERY
        if is_faq_category_request or navigation_target is not None:
            return ChatbotIntent.NAVIGATION
        if match is not None or intent == ChatbotIntent.FAQ_QUERY.value:
            return ChatbotIntent.FAQ_QUERY
        try:
            return ChatbotIntent(str(intent))
        except ValueError:
            return deterministic_intent

    @classmethod
    def _is_advisor_request(cls, content: str) -> bool:
        normalized = cls.normalize_text(content)
        if re.search(
            r"\b(?:horario|horarios|telefono|numero|contacto|canal|direccion)\b",
            normalized,
        ) and re.search(
            r"\b(?:que|cual|como|donde|horario|telefono|contacto)\b", normalized
        ):
            return False
        return any(pattern.search(normalized) for pattern in _ADVISOR_REQUEST_PATTERNS)

    @classmethod
    def _is_unhelpful_follow_up(cls, content: str) -> bool:
        return bool(_UNHELPFUL_FOLLOW_UP_PATTERN.search(cls.normalize_text(content)))

    @classmethod
    def _ticket_query_request(cls, content: str) -> TicketQueryRequest | None:
        normalized = cls.normalize_text(content)
        codes = list(dict.fromkeys(_TICKET_CODE_PATTERN.findall(content)))
        codes = [code.upper() for code in codes]
        has_code_marker = bool(re.search(r"\btck-", normalized))
        code = codes[0] if len(codes) == 1 else None
        has_ticket_subject = bool(
            re.search(r"\b(?:tickets?|casos?|solicitud(?:es)?)\b", normalized)
            or re.search(r"\b(?:mi|mis|mio|mia|mios|mias)\s+reclamos?\b", normalized)
        )
        has_explicit_ticket_reference = bool(
            re.search(r"\b(?:tickets?|casos?)\b", normalized)
            or re.search(
                r"\b(?:mi|mis|mio|mia|mios|mias)\s+solicitud(?:es)?\b", normalized
            )
            or re.search(r"\b(?:mi|mis|mio|mia|mios|mias)\s+reclamos?\b", normalized)
        )
        plural_ticket_reference = bool(
            re.search(
                r"\bmis\s+(?:tickets?|casos?|solicitudes?|reclamos?)\b", normalized
            )
        )
        page_match = re.search(r"\bpagina\s+(\d+)\b", normalized)
        page = min(int(page_match.group(1)), 10_000) if page_match else 1
        position = cls._ticket_position(content)
        asks_assignment = bool(
            re.search(
                r"\b(?:asignad[oa]s?|asignacion|atendiendo|atendid[oa]s?|tom[oó]|"
                r"se\s+hizo\s+cargo)\b|"
                r"\b(?:asesor|agente|representante)\b.{0,35}"
                r"\b(?:atiende|atendio|atendiendo|asignado|tomo)\b",
                normalized,
            )
        )
        asks_subject = bool(
            re.search(r"\b(?:que|cual|cuales)\s+(?:asunto|titulo)\b", normalized)
            or re.search(
                r"\b(?:dime|muestrame|ver|consultar)\b.{0,35}"
                r"\b(?:asunto|titulo)\b",
                normalized,
            )
        )
        asks_status = (has_explicit_ticket_reference or has_code_marker) and bool(
            re.search(
                r"\b(?:estado|situacion|seguimiento)\b|\bcomo\s+(?:va|esta|sigue)\b",
                normalized,
            )
        )
        asks_attended_tickets = plural_ticket_reference and bool(
            asks_assignment
            or re.search(
                r"\b(?:ya\s+fueron\s+atendid[oa]s?|atendid[oa]s?|"
                r"respondid[oa]s?|contestad[oa]s?)\b",
                normalized,
            )
            or asks_status
        )

        if asks_attended_tickets:
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_LIST,
                page=page,
                requested_fields=frozenset({"CODE", "SUBJECT", "STATUS", "ASSIGNMENT"}),
            )

        if re.search(
            r"\b(?:que|cuales).{0,40}\b(?:esperan|esperando|pendiente|pendientes)\b"
            r".{0,40}\b(?:mi parte|de mi parte|mi respuesta|mi accion|"
            r"mi confirmacion)\b",
            normalized,
        ) or re.search(
            r"\b(?:pendiente|pendientes)\b.{0,30}\b"
            r"(?:de mi parte|mi respuesta|mi accion)\b",
            normalized,
        ):
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.CLIENT_ACTION_REQUIRED,
                query_type=ChatbotTicketQueryType.CLIENT_ACTION_REQUIRED,
                statuses=frozenset({TicketStatus.PENDIENTE_CLIENTE}),
                page=page,
            )

        asks_no_response = has_ticket_subject and bool(
            re.search(
                r"\b(?:nadie|no\s+me|no\s+han|todavia\s+no|aun\s+no)\b"
                r".{0,45}\b(?:responde|respondio|contesta|contesto)\b",
                normalized,
            )
        )
        if asks_no_response:
            if has_code_marker and (len(codes) != 1 or code is None):
                return cls._clarify_ticket_code_request(
                    page,
                    action=ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS,
                    query_type=ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS,
                )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS,
                query_type=ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS,
                tracking_code=code,
                page=page,
            )

        forgot_ticket_code = has_ticket_subject and bool(
            re.search(
                r"\b(?:no\s+(?:recuerdo|recuerdas|tengo|se)|olvide|olvido)\b"
                r".{0,35}\b(?:codigo|numero)\b",
                normalized,
            )
        )
        if forgot_ticket_code:
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_LIST,
                page=page,
            )

        asks_what_answered = bool(
            re.search(
                r"\b(?:que|cual).{0,35}\b(?:respondio|contesto)\b|"
                r"\b(?:respuestas?|comentarios?)\b.{0,25}\b(?:asesor|ticket)\b|"
                r"\bdonde\b.{0,25}\b(?:veo|consulto|reviso)\b.{0,25}"
                r"\b(?:respuestas?|comentarios?)\b",
                normalized,
            )
        )
        asks_if_answered = (
            bool(
                re.search(
                    r"\b(?:ya|asesor|respondio|contesto).{0,35}"
                    r"\b(?:respondio|contesto|asesor)\b",
                    normalized,
                )
            )
            and not asks_what_answered
        )
        if asks_what_answered or asks_if_answered:
            query_type = (
                ChatbotTicketQueryType.ADVISOR_REPLY
                if asks_what_answered
                else ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS
            )
            action = (
                ChatbotSuggestedAction.LATEST_ADVISOR_RESPONSE
                if asks_what_answered
                else ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS
            )
            if has_code_marker and (len(codes) != 1 or code is None):
                return cls._clarify_ticket_code_request(
                    page,
                    action=action,
                    query_type=query_type,
                )
            return TicketQueryRequest(
                action=action,
                query_type=query_type,
                tracking_code=code,
                page=page,
            )

        if asks_assignment:
            if has_code_marker and (len(codes) != 1 or code is None):
                return cls._clarify_ticket_code_request(
                    page,
                    action=ChatbotSuggestedAction.TICKET_STATUS,
                    query_type=ChatbotTicketQueryType.TICKET_STATUS,
                )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.TICKET_STATUS,
                query_type=ChatbotTicketQueryType.TICKET_STATUS,
                tracking_code=code,
                page=page,
                position=position,
                requested_fields=cls._requested_ticket_fields(
                    content, default=frozenset({"CODE", "ASSIGNMENT"})
                ),
            )

        if asks_status:
            if has_code_marker and (len(codes) != 1 or code is None):
                return cls._clarify_ticket_code_request(
                    page,
                    action=ChatbotSuggestedAction.TICKET_STATUS,
                    query_type=ChatbotTicketQueryType.TICKET_STATUS,
                )
            if plural_ticket_reference:
                return TicketQueryRequest(
                    action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                    query_type=ChatbotTicketQueryType.TICKET_LIST,
                    page=page,
                    requested_fields=frozenset(
                        {"CODE", "SUBJECT", "STATUS", "ASSIGNMENT"}
                    ),
                )
            if code is None and position is None:
                return cls._clarify_ticket_code_request(
                    page,
                    action=ChatbotSuggestedAction.TICKET_STATUS,
                    query_type=ChatbotTicketQueryType.TICKET_STATUS,
                )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.TICKET_STATUS,
                query_type=ChatbotTicketQueryType.TICKET_STATUS,
                tracking_code=code,
                page=page,
                position=position,
                requested_fields=cls._requested_ticket_fields(
                    content, default=frozenset({"CODE", "STATUS"})
                ),
            )

        if asks_subject and has_ticket_subject:
            if plural_ticket_reference and position is None:
                return TicketQueryRequest(
                    action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                    query_type=ChatbotTicketQueryType.TICKET_LIST,
                    page=page,
                    requested_fields=frozenset({"CODE", "SUBJECT"}),
                )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.TICKET_STATUS,
                query_type=ChatbotTicketQueryType.TICKET_STATUS,
                tracking_code=code,
                page=page,
                position=position,
                requested_fields=frozenset({"CODE", "SUBJECT"}),
            )

        asks_count = bool(
            re.search(
                r"\b(?:cuantos?|cuantas?)\s+"
                r"(?:tickets?|casos?|solicitud(?:es)?|reclamos?)\b|"
                r"\b(?:tickets?|casos?|solicitud(?:es)?|reclamos?)\s+tengo\b",
                normalized,
            )
        )
        if asks_count:
            statuses = (
                _OPEN_TICKET_STATUSES
                if re.search(r"\b(?:abiertos?|activas?)\b", normalized)
                else frozenset({TicketStatus.PENDIENTE_CLIENTE})
                if re.search(r"\bpendientes?\b", normalized)
                else None
            )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.COUNT_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_COUNT,
                statuses=statuses,
            )

        asks_latest = bool(
            re.search(r"\b(?:ultimo|ultima)\s+(?:ticket|caso|solicitud)\b", normalized)
        )
        if asks_latest:
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                query_type=ChatbotTicketQueryType.LATEST_TICKET,
            )

        asks_list = has_ticket_subject and bool(
            re.search(
                r"\b(?:mis|mios|mias|muestrame|muestra|mostrar|lista|listar|ver|"
                r"consultar|recientes?|abiertos?|activas?)\b",
                normalized,
            )
        )
        if asks_list:
            statuses = (
                _OPEN_TICKET_STATUSES
                if re.search(r"\b(?:abiertos?|activas?)\b", normalized)
                else frozenset({TicketStatus.PENDIENTE_CLIENTE})
                if re.search(r"\bpendientes?\b", normalized)
                else None
            )
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_LIST,
                statuses=statuses,
                page=page,
            )

        if has_code_marker:
            return cls._clarify_ticket_code_request(page)
        if has_ticket_subject and re.search(r"\b(?:codigo|numero)\b", normalized):
            return cls._clarify_ticket_code_request(page)
        return None

    @staticmethod
    def _clarify_ticket_code_request(
        page: int,
        *,
        action: ChatbotSuggestedAction | None = None,
        query_type: ChatbotTicketQueryType | None = None,
    ) -> TicketQueryRequest:
        return TicketQueryRequest(
            action=ChatbotSuggestedAction.CLARIFY_TICKET_CODE,
            query_type=ChatbotTicketQueryType.CLARIFY_TICKET_CODE,
            page=page,
            clarification_action=action,
            clarification_query_type=query_type,
        )

    @classmethod
    def _resume_clarified_ticket_query(
        cls,
        content: str,
        conversation: Conversation,
        request: TicketQueryRequest | None,
    ) -> TicketQueryRequest | None:
        if (
            request is None
            or request.query_type != ChatbotTicketQueryType.CLARIFY_TICKET_CODE
        ):
            return request
        codes = list(dict.fromkeys(_TICKET_CODE_PATTERN.findall(content)))
        pending = (conversation.context_json or {}).get("pending_ticket_query")
        if len(codes) != 1 or not isinstance(pending, dict):
            return request
        try:
            action = ChatbotSuggestedAction(pending.get("action"))
            query_type = ChatbotTicketQueryType(pending.get("query_type"))
        except ValueError:
            return request
        supported_queries = {
            ChatbotSuggestedAction.TICKET_STATUS: (
                ChatbotTicketQueryType.TICKET_STATUS
            ),
            ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS: (
                ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS
            ),
            ChatbotSuggestedAction.LATEST_ADVISOR_RESPONSE: (
                ChatbotTicketQueryType.ADVISOR_REPLY
            ),
        }
        if supported_queries.get(action) != query_type:
            return request
        return TicketQueryRequest(
            action=action,
            query_type=query_type,
            tracking_code=codes[0].upper(),
            page=request.page,
        )

    @classmethod
    def _contextual_ticket_query_request(
        cls,
        content: str,
        context: dict[str, object],
    ) -> TicketQueryRequest | None:
        normalized = normalize_text(content).strip(" .,!?:;¿¡")
        codes = context.get("last_ticket_query_codes")
        if not isinstance(codes, list):
            return None
        safe_codes = [code for code in codes if isinstance(code, str)]
        if not safe_codes:
            return None
        asks_code = bool(re.search(r"\b(?:codigo|numero)\b", normalized))
        asks_assignment = bool(
            re.search(
                r"\b(?:asesor|agente|representante|asignad[oa]s?|asignacion|"
                r"atendiendo|atendid[oa]s?|tomo)\b",
                normalized,
            )
        )
        asks_reply_content = bool(
            re.search(
                r"\b(?:que|cual).{0,35}\b(?:respondio|contesto)\b|"
                r"\b(?:respuesta|comentario)\b.{0,25}\b(?:asesor|ticket)\b",
                normalized,
            )
        )
        asks_reply_status = bool(
            re.search(
                r"\b(?:respondio|contesto|respondieron|contestaron)\b", normalized
            )
        )
        asks_ticket_field = bool(
            re.search(
                r"\b(?:asunto|estado|situacion|seguimiento|codigo|numero|"
                r"asesor|asignad[oa]s?|asignacion|atendiendo|atendid[oa]s?|"
                r"respuesta|comentario|respondio|contesto)\b",
                normalized,
            )
        )
        is_other = bool(re.search(r"\b(?:el|la)\s+otro\b", normalized))
        ordinal_match = re.search(
            r"\b(?:el|la|del)?\s*(primer[oa]?|segund[oa]?|tercer[oa]?|"
            r"cuart[oa]?|quint[oa]?|[1-5](?:ro|do|er|to)?)\b",
            normalized,
        )
        is_reference = bool(
            re.search(
                r"\b(?:ese|este|el|la)\s+(?:ticket|caso|solicitud)\b|"
                r"\b(?:el|la)\s+(?:primero|primera|segundo|segunda)\b|"
                r"\b(?:lo|ese|este|el mismo|la misma)\b",
                normalized,
            )
        )
        explicit_ticket_reference = bool(
            re.search(r"\b(?:ese|este)\s+(?:ticket|caso|solicitud)\b", normalized)
        )
        if not (asks_ticket_field or is_other or is_reference or ordinal_match):
            return None
        query_type = (
            ChatbotTicketQueryType.ADVISOR_REPLY
            if asks_reply_content
            else ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS
            if asks_reply_status
            else ChatbotTicketQueryType.TICKET_STATUS
        )
        action = (
            ChatbotSuggestedAction.LATEST_ADVISOR_RESPONSE
            if query_type == ChatbotTicketQueryType.ADVISOR_REPLY
            else ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS
            if query_type == ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS
            else ChatbotSuggestedAction.TICKET_STATUS
        )
        pending_query = context.get("pending_ticket_query")
        if isinstance(pending_query, dict) and (
            ordinal_match is not None or is_other or is_reference
        ):
            try:
                pending_action = ChatbotSuggestedAction(pending_query.get("action"))
                pending_query_type = ChatbotTicketQueryType(
                    pending_query.get("query_type")
                )
            except ValueError:
                pending_action = None
                pending_query_type = None
            supported_queries = {
                ChatbotSuggestedAction.TICKET_STATUS: (
                    ChatbotTicketQueryType.TICKET_STATUS
                ),
                ChatbotSuggestedAction.ADVISOR_RESPONSE_STATUS: (
                    ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS
                ),
                ChatbotSuggestedAction.LATEST_ADVISOR_RESPONSE: (
                    ChatbotTicketQueryType.ADVISOR_REPLY
                ),
            }
            if supported_queries.get(pending_action) == pending_query_type:
                action = pending_action
                query_type = pending_query_type
        current_index = context.get("last_ticket_query_index")
        current_index = (
            current_index
            if isinstance(current_index, int) and 0 <= current_index < len(safe_codes)
            else None
        )
        if ordinal_match is not None:
            ordinal = normalize_text(ordinal_match.group(1))
            ordinal_to_index = {
                "primero": 0,
                "primera": 0,
                "primer": 0,
                "1ro": 0,
                "1er": 0,
                "segundo": 1,
                "segunda": 1,
                "2do": 1,
                "tercero": 2,
                "tercera": 2,
                "tercer": 2,
                "3er": 2,
                "3ro": 2,
                "cuarto": 3,
                "cuarta": 3,
                "4to": 3,
                "quinto": 4,
                "quinta": 4,
                "5to": 4,
            }
            index = ordinal_to_index.get(ordinal)
            selected_index = index if index is not None else 0
            if selected_index >= len(safe_codes):
                return TicketQueryRequest(
                    action=action,
                    query_type=query_type,
                    position=selected_index + 1,
                    requested_fields=cls._requested_ticket_fields(
                        content, default=frozenset({"CODE", "SUBJECT", "STATUS"})
                    ),
                )
        elif asks_code and len(safe_codes) > 1 and current_index is None:
            return cls._clarify_ticket_code_request(
                1, action=action, query_type=query_type
            )
        elif is_other:
            if len(safe_codes) < 2:
                return None
            selected_index = (
                1 if current_index is None else (current_index + 1) % len(safe_codes)
            )
        elif current_index is not None:
            selected_index = current_index
        elif len(safe_codes) == 1:
            selected_index = 0
        elif explicit_ticket_reference:
            selected_index = 0
        else:
            return TicketQueryRequest(
                action=action,
                query_type=query_type,
                requested_fields=cls._requested_ticket_fields(
                    content, default=frozenset({"CODE", "SUBJECT", "STATUS"})
                ),
            )
        default_fields = (
            frozenset({"CODE", "ASSIGNMENT"})
            if asks_assignment
            else frozenset({"CODE", "RESPONSE_STATUS"})
            if asks_reply_status
            else frozenset({"CODE", "RESPONSE"})
            if asks_reply_content
            else frozenset({"CODE", "SUBJECT", "STATUS"})
        )
        requested_fields = cls._requested_ticket_fields(content, default=default_fields)
        return TicketQueryRequest(
            action=action,
            query_type=query_type,
            tracking_code=safe_codes[selected_index],
            requested_fields=requested_fields,
        )

    @staticmethod
    def _provider_dialogue_context(
        recent_messages: list[ChatMessage],
        context: dict[str, object],
        draft: ChatbotTicketDraft,
        *,
        include_user_turns: bool = True,
    ) -> tuple[dict[str, str], ...]:
        turns: list[dict[str, str]] = []
        if include_user_turns:
            for index, message in enumerate(recent_messages):
                if message.sender_type != SenderType.USER:
                    continue
                following_bot = next(
                    (
                        item
                        for item in recent_messages[index + 1 :]
                        if item.sender_type == SenderType.BOT
                    ),
                    None,
                )
                is_ticket_draft_turn = following_bot is not None and (
                    following_bot.intent is not None
                    and (
                        following_bot.intent.startswith("TICKET_DRAFT")
                        or following_bot.intent == "ADVISOR_TICKET_DRAFT"
                    )
                )
                is_ticket_data_turn = (
                    TicketDraftService._has_field_label(message.content)
                    or _TICKET_CODE_PATTERN.search(message.content) is not None
                    or re.search(
                        r"\b(?:tickets?|solicitudes?|reclamos?|casos?)\b",
                        normalize_text(message.content),
                    )
                    is not None
                    or ChatbotService._is_ticket_request(message.content)
                    or ChatbotService._ticket_query_request(message.content) is not None
                )
                if is_ticket_draft_turn or is_ticket_data_turn:
                    continue
                content = _PROVIDER_TICKET_REFERENCE_PATTERN.sub(
                    "referencia de ticket", message.content
                )
                content = minimize_provider_input(content).strip()[:300]
                if content:
                    turns.append({"role": "user", "content": content})
        turns = turns[-2:]
        references = context.get("last_ticket_query_codes")
        reference_count = (
            sum(isinstance(code, str) for code in references)
            if isinstance(references, list)
            else 0
        )
        state_parts = [f"referencias_de_ticket_disponibles={reference_count}"]
        if draft.status in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
        }:
            missing = ",".join(item.value for item in draft.missing_fields)
            state_parts.append(f"borrador_activo=true;campos_pendientes={missing}")
        turns.append({"role": "state", "content": ";".join(state_parts)})
        return tuple(turns)

    @staticmethod
    def _merge_ai_traces(*traces: dict[str, object] | None) -> dict[str, object]:
        useful = [trace for trace in traces if isinstance(trace, dict)]
        merged: dict[str, object] = {}
        for trace in useful:
            merged.update(trace)
        call_count = 0
        for trace in useful:
            current = trace.get("provider_call_count")
            if isinstance(current, int) and not isinstance(current, bool):
                call_count += max(current, 0)
            elif trace.get("provider_called") is True:
                call_count += 1
        merged["provider_call_count"] = call_count
        merged["provider_called"] = call_count > 0
        for token_field in ("input_tokens", "output_tokens", "total_tokens"):
            values = [
                trace.get(token_field)
                for trace in useful
                if isinstance(trace.get(token_field), int)
                and not isinstance(trace.get(token_field), bool)
            ]
            if values:
                merged[token_field] = sum(values)
        costs = [
            trace.get("estimated_cost_usd")
            for trace in useful
            if isinstance(trace.get("estimated_cost_usd"), int | float)
            and not isinstance(trace.get("estimated_cost_usd"), bool)
        ]
        if costs:
            merged["estimated_cost_usd"] = sum(float(value) for value in costs)
        stages = [
            {
                "provider_status": trace.get("provider_status"),
                "error_code": trace.get("error_code"),
                "cache_hit": trace.get("cache_hit") is True,
            }
            for trace in useful
            if trace.get("provider_called") is True
            or trace.get("provider_status") is not None
        ]
        if stages:
            merged["provider_stages"] = stages
        return merged

    @staticmethod
    def _provider_call_count(trace: dict[str, object] | None) -> int:
        if not isinstance(trace, dict):
            return 0
        count = trace.get("provider_call_count")
        if isinstance(count, int) and not isinstance(count, bool):
            return max(count, 0)
        return 1 if trace.get("provider_called") is True else 0

    @staticmethod
    def _draft_answer_matches_missing_field(
        answer: str,
        draft: ChatbotTicketDraft,
    ) -> bool:
        if draft.validation_errors or not draft.missing_fields:
            return False
        field = draft.missing_fields[0]
        normalized = normalize_text(answer)
        patterns = {
            ChatbotTicketDraftField.CATEGORY_ID: r"\bcategoria\b",
            ChatbotTicketDraftField.SUBJECT: r"\b(?:asunto|solicitar|necesitas)\b",
            ChatbotTicketDraftField.DESCRIPTION: (
                r"\b(?:describe|descripcion|problema|detalle)\b"
            ),
            ChatbotTicketDraftField.PRIORITY: r"\bprioridad\b",
        }
        pattern = patterns.get(field)
        return bool(pattern and re.search(pattern, normalized))

    @staticmethod
    def _explicit_ticket_creation_request(content: str) -> bool:
        normalized = normalize_text(content)
        return bool(
            re.search(
                r"\b(?:crear|abrir|registrar|generar|iniciar|levantar|presentar)\b"
                r".{0,45}\b(?:tickets?|solicitudes?|reclamos?|casos?)\b",
                normalized,
            )
        )

    @classmethod
    def _ticket_query_from_tool_call(
        cls,
        content: str,
        arguments: dict[str, object],
        context: dict[str, object],
        existing: TicketQueryRequest | None,
    ) -> TicketQueryRequest | None:
        operation = arguments.get("operation")
        if operation not in {"LIST", "COUNT", "GET"}:
            return None
        normalized = cls.normalize_text(content)
        asks_count = bool(re.search(r"\b(?:cuantos?|cuantas?)\b", normalized))
        asks_list = bool(
            re.search(
                r"\b(?:muestrame|mostrar|lista|listar|ver|recientes?)\b|"
                r"\b(?:mis|mios|mias)\s+(?:tickets?|solicitudes?|casos?)\b",
                normalized,
            )
        )
        asks_detail = bool(
            re.search(
                r"\b(?:codigo|numero|asunto|titulo|estado|situacion|seguimiento|"
                r"descripcion|detalle|asesor|asignad[oa]s?|atendiendo|"
                r"atendid[oa]s?|respuesta|comentario|respondio|contesto|"
                r"primero|primera|segundo|segunda|tercero|tercera|cuarto|"
                r"cuarta|quinto|quinta)\b",
                normalized,
            )
        )
        if (
            operation == "COUNT"
            and not asks_count
            or operation == "LIST"
            and asks_count
            or operation == "LIST"
            and not asks_list
            and existing is None
            or operation == "GET"
            and not asks_detail
        ):
            if existing is None:
                return None
            operation = (
                "COUNT"
                if existing.query_type == ChatbotTicketQueryType.TICKET_COUNT
                else "LIST"
                if existing.query_type
                in {
                    ChatbotTicketQueryType.TICKET_LIST,
                    ChatbotTicketQueryType.LATEST_TICKET,
                }
                else "GET"
            )

        status_value = arguments.get("status")
        statuses = cls._requested_tool_statuses(status_value, content, existing)
        page_value = arguments.get("page")
        page = (
            existing.page
            if existing is not None
            else page_value
            if isinstance(page_value, int) and 1 <= page_value <= 10_000
            else 1
        )
        if existing is not None and existing.page != 1:
            page = existing.page

        if operation == "LIST":
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.LIST_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_LIST,
                statuses=statuses,
                page=page,
                requested_fields=frozenset({"CODE", "SUBJECT", "STATUS"}),
            )
        if operation == "COUNT":
            return TicketQueryRequest(
                action=ChatbotSuggestedAction.COUNT_MY_TICKETS,
                query_type=ChatbotTicketQueryType.TICKET_COUNT,
                statuses=statuses,
                page=page,
            )

        direct_codes = list(
            dict.fromkeys(
                code.upper() for code in _TICKET_CODE_PATTERN.findall(content)
            )
        )
        if len(direct_codes) > 1:
            return cls._clarify_ticket_code_request(page)
        stored_codes = context.get("last_ticket_query_codes")
        stored_codes = (
            [code for code in stored_codes if isinstance(code, str)]
            if isinstance(stored_codes, list)
            else []
        )
        tracking_code = direct_codes[0] if direct_codes else None
        proposed_code = arguments.get("tracking_code")
        if (
            tracking_code is None
            and isinstance(proposed_code, str)
            and proposed_code in stored_codes
        ):
            tracking_code = proposed_code

        position = arguments.get("position")
        if not isinstance(position, int) or not 1 <= position <= _MAX_CHAT_TICKETS:
            position = (
                existing.position
                if existing is not None and existing.position is not None
                else cls._ticket_position(content)
            )
        asks_code = bool(re.search(r"\b(?:codigo|numero)\b", normalized))
        if tracking_code is None and position is not None and stored_codes:
            if position <= len(stored_codes):
                tracking_code = stored_codes[position - 1]
            else:
                return cls._clarify_ticket_code_request(page)
        elif tracking_code is None and asks_code and len(stored_codes) == 1:
            tracking_code = stored_codes[0]
        elif tracking_code is None and asks_code and len(stored_codes) > 1:
            clarification = cls._clarify_ticket_code_request(page)
            return TicketQueryRequest(
                action=clarification.action,
                query_type=clarification.query_type,
                page=page,
            )
        if tracking_code is None and existing is not None:
            tracking_code = existing.tracking_code
        requested_fields = cls._requested_ticket_fields(
            content,
            default=frozenset({"CODE", "SUBJECT", "STATUS"}),
        )
        if tracking_code is None and position is not None:
            return TicketQueryRequest(
                action=(
                    existing.action
                    if existing is not None
                    else ChatbotSuggestedAction.TICKET_STATUS
                ),
                query_type=(
                    existing.query_type
                    if existing is not None
                    else ChatbotTicketQueryType.TICKET_STATUS
                ),
                position=position,
                page=page,
                requested_fields=requested_fields,
            )
        if tracking_code is None:
            return cls._clarify_ticket_code_request(
                page,
                action=ChatbotSuggestedAction.TICKET_STATUS,
                query_type=ChatbotTicketQueryType.TICKET_STATUS,
            )

        proposed_fields = arguments.get("fields")
        if isinstance(proposed_fields, list) and proposed_fields:
            selected_fields = requested_fields & set(proposed_fields)
            if selected_fields:
                requested_fields = frozenset(selected_fields)
        return TicketQueryRequest(
            action=ChatbotSuggestedAction.TICKET_STATUS,
            query_type=ChatbotTicketQueryType.TICKET_STATUS,
            tracking_code=tracking_code,
            page=page,
            requested_fields=requested_fields,
        )

    @classmethod
    def _requested_tool_statuses(
        cls,
        value: object,
        content: str,
        existing: TicketQueryRequest | None,
    ) -> frozenset[TicketStatus] | None:
        normalized = cls.normalize_text(content)
        if existing is not None and existing.statuses is not None:
            return existing.statuses
        if value in (None, "ALL"):
            if re.search(r"\bpendientes?\b", normalized):
                return frozenset({TicketStatus.PENDIENTE_CLIENTE})
            if re.search(r"\b(?:abiertos?|activas?)\b", normalized):
                return _OPEN_TICKET_STATUSES
            return None
        if value == "OPEN":
            return (
                _OPEN_TICKET_STATUSES
                if re.search(r"\b(?:abiertos?|activas?)\b", normalized)
                else None
            )
        try:
            selected = TicketStatus(str(value))
        except ValueError:
            return None
        labels = {
            TicketStatus.PENDIENTE_CLIENTE: r"\bpendientes?\b",
        }
        pattern = labels.get(selected, rf"\b{normalize_text(selected.value)}\b")
        if re.search(pattern, normalized):
            return frozenset({selected})
        return None

    @staticmethod
    def _ticket_position(content: str) -> int | None:
        normalized = normalize_text(content)
        ordinals = (
            (r"\b(?:el|la|del)?\s*primer[oa]?\b", 1),
            (r"\b(?:el|la|del)?\s*segund[oa]?\b", 2),
            (r"\b(?:el|la|del)?\s*tercer[oa]?\b", 3),
            (r"\b(?:el|la|del)?\s*cuart[oa]?\b", 4),
            (r"\b(?:el|la|del)?\s*quint[oa]?\b", 5),
        )
        return next(
            (
                position
                for pattern, position in ordinals
                if re.search(pattern, normalized)
            ),
            None,
        )

    @staticmethod
    def _supported_provider_query(query: str, content: str) -> bool:
        normalized_query = normalize_text(query).strip()
        normalized_content = normalize_text(content)
        if not normalized_query:
            return False
        if normalized_query in normalized_content:
            return True
        query_terms = {term for term in normalized_query.split() if len(term) > 3}
        content_terms = set(normalized_content.split())
        return bool(query_terms) and query_terms <= content_terms

    @classmethod
    def _requested_ticket_fields(
        cls,
        content: str,
        *,
        default: frozenset[str],
    ) -> frozenset[str]:
        normalized = cls.normalize_text(content)
        fields: set[str] = set()
        if re.search(r"\b(?:codigo|numero)\b", normalized):
            fields.add("CODE")
        if re.search(r"\b(?:asunto|titulo)\b", normalized):
            fields.add("SUBJECT")
        if re.search(r"\b(?:estado|situacion|seguimiento)\b", normalized):
            fields.add("STATUS")
        if re.search(
            r"\b(?:asignad[oa]s?|asignacion|atendiendo|atendid[oa]s?|tomo)\b|"
            r"\b(?:asesor|agente|representante)\b.{0,35}"
            r"\b(?:atiende|atendio|atendiendo|asignado|tomo)\b",
            normalized,
        ):
            fields.add("ASSIGNMENT")
        if re.search(
            r"\b(?:respondio|contesto|respondieron|contestaron)\b", normalized
        ):
            fields.add("RESPONSE_STATUS")
        if re.search(r"\b(?:respuesta|comentario)\b", normalized):
            fields.add("RESPONSE")
        if re.search(r"\b(?:descripcion|detalle)\b", normalized):
            fields.add("DESCRIPTION")
        return frozenset(fields) if fields else default

    @staticmethod
    def _is_ticket_query_intent(intent: str | None) -> bool:
        return intent in {query_type.value for query_type in ChatbotTicketQueryType}

    def _ticket_query_response(
        self,
        session: Session,
        current_user: AuthenticatedUser | None,
        request: TicketQueryRequest,
    ) -> dict[str, object]:
        if current_user is None:
            payload = ChatbotTicketQuery(
                query_type=ChatbotTicketQueryType.AUTHENTICATION_REQUIRED,
                authentication_required=True,
                authentication_actions=[
                    ChatbotAuthenticationAction.LOGIN,
                    ChatbotAuthenticationAction.REGISTER,
                ],
            )
            return self._ticket_query_result(
                "Inicia sesión o regístrate para consultar tus tickets. "
                "Después podrás reanudar esta conversación.",
                ChatbotSuggestedAction.AUTHENTICATION_REQUIRED,
                payload,
                resolved=False,
            )

        if request.query_type == ChatbotTicketQueryType.CLARIFY_TICKET_CODE:
            payload = ChatbotTicketQuery(
                query_type=request.query_type,
                found=None,
            )
            result = self._ticket_query_result(
                "Indícame un solo código de seguimiento completo (por ejemplo, "
                "TCK-20261010-AB12CD34EF) para revisar el ticket.",
                request.action,
                payload,
                resolved=False,
                requires_clarification=True,
            )
            result["pending_question"] = (
                "Indícame un solo código de seguimiento completo."
            )
            if (
                request.clarification_action is not None
                and request.clarification_query_type is not None
            ):
                result["pending_ticket_query"] = {
                    "action": request.clarification_action.value,
                    "query_type": request.clarification_query_type.value,
                }
            return result

        if request.query_type in {
            ChatbotTicketQueryType.TICKET_LIST,
            ChatbotTicketQueryType.TICKET_COUNT,
            ChatbotTicketQueryType.CLIENT_ACTION_REQUIRED,
        }:
            page_size = _MAX_CHAT_TICKETS
            result_page = self.tickets.list_mine(
                session,
                current_user,
                page=request.page,
                page_size=page_size,
                statuses=set(request.statuses)
                if request.statuses is not None
                else None,
            )
            total = result_page.total
            if request.query_type == ChatbotTicketQueryType.TICKET_COUNT:
                answer = f"Tienes {total} ticket{'' if total == 1 else 's'}."
                if request.statuses == _OPEN_TICKET_STATUSES:
                    answer = (
                        f"Tienes {total} ticket{'' if total == 1 else 's'} abiertos."
                    )
                elif request.statuses == frozenset({TicketStatus.PENDIENTE_CLIENTE}):
                    answer = (
                        f"Tienes {total} ticket{'' if total == 1 else 's'} "
                        "pendiente" + ("." if total == 1 else "s.")
                    )
                payload = ChatbotTicketQuery(
                    query_type=request.query_type,
                    found=True,
                    total=total,
                )
            else:
                cards = [self._ticket_card(item) for item in result_page.items]
                if request.query_type == ChatbotTicketQueryType.CLIENT_ACTION_REQUIRED:
                    answer = (
                        "No hay solicitudes pendientes de tu respuesta."
                        if total == 0
                        else "Estas solicitudes están esperando algo de tu parte."
                    )
                elif request.statuses == _OPEN_TICKET_STATUSES:
                    answer = (
                        "No tienes tickets abiertos."
                        if total == 0
                        else "Estos son tus tickets abiertos más recientes."
                    )
                elif "ASSIGNMENT" in (request.requested_fields or frozenset()):
                    assigned_count = sum(
                        item.assigned_advisor_id is not None
                        for item in result_page.items
                    )
                    shown_count = len(result_page.items)
                    if total == 0:
                        answer = "No tienes tickets registrados para revisar."
                    elif assigned_count == shown_count:
                        answer = (
                            f"Los {shown_count} tickets mostrados tienen un asesor "
                            "asignado; las tarjetas incluyen su estado actual."
                        )
                    elif assigned_count == 0:
                        answer = (
                            f"Ninguno de los {shown_count} tickets mostrados tiene "
                            "un asesor asignado; las tarjetas incluyen su estado."
                        )
                    else:
                        answer = (
                            f"{assigned_count} de los {shown_count} tickets mostrados "
                            "tienen un asesor asignado; las tarjetas incluyen "
                            "su estado."
                        )
                elif "STATUS" in (request.requested_fields or frozenset()):
                    answer = (
                        "No tienes tickets registrados para revisar su estado."
                        if total == 0
                        else "Estos son tus tickets y su estado actual."
                    )
                elif "SUBJECT" in (request.requested_fields or frozenset()):
                    answer = (
                        "No tienes tickets registrados para revisar sus asuntos."
                        if total == 0
                        else "Estos son tus tickets y sus asuntos."
                    )
                else:
                    answer = (
                        "No tienes tickets registrados."
                        if total == 0
                        else "Estos son tus tickets más recientes."
                    )
                payload = ChatbotTicketQuery(
                    query_type=request.query_type,
                    found=total > 0,
                    tickets=cards,
                    total=total,
                    pagination=ChatbotTicketPagination(
                        page=result_page.page,
                        page_size=result_page.page_size,
                        total_pages=result_page.total_pages,
                    ),
                )
            result = self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )
            result["ticket_reference_codes"] = [
                item.tracking_code for item in result_page.items
            ]
            return result

        if request.query_type == ChatbotTicketQueryType.LATEST_TICKET:
            page_result = self.tickets.list_mine(
                session, current_user, page=1, page_size=1
            )
            ticket = page_result.items[0] if page_result.items else None
            payload = ChatbotTicketQuery(
                query_type=request.query_type,
                found=ticket is not None,
                ticket=self._ticket_card(ticket) if ticket is not None else None,
            )
            answer = (
                f"Tu último ticket es {ticket.tracking_code}: {ticket.subject}."
                if ticket is not None
                else "No tienes tickets registrados."
            )
            result = self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )
            result["ticket_reference_codes"] = (
                [ticket.tracking_code] if ticket is not None else []
            )
            return result

        ticket = None
        if request.tracking_code is None:
            owned_tickets = self.tickets.list_mine(
                session, current_user, page=1, page_size=_MAX_CHAT_TICKETS
            )
            if request.position is not None:
                if request.position <= len(owned_tickets.items):
                    ticket = owned_tickets.items[request.position - 1]
                else:
                    payload = ChatbotTicketQuery(
                        query_type=request.query_type,
                        found=False,
                    )
                    result = self._ticket_query_result(
                        "No encuentro un ticket en esa posición de tu lista.",
                        request.action,
                        payload,
                        resolved=True,
                    )
                    result["ticket_reference_codes"] = [
                        item.tracking_code for item in owned_tickets.items
                    ]
                    return result
            elif len(owned_tickets.items) == 1:
                ticket = owned_tickets.items[0]
            elif len(owned_tickets.items) > 1:
                cards = [self._ticket_card(item) for item in owned_tickets.items]
                payload = ChatbotTicketQuery(
                    query_type=ChatbotTicketQueryType.TICKET_LIST,
                    found=True,
                    tickets=cards,
                    total=owned_tickets.total,
                    pagination=ChatbotTicketPagination(
                        page=owned_tickets.page,
                        page_size=owned_tickets.page_size,
                        total_pages=owned_tickets.total_pages,
                    ),
                )
                result = self._ticket_query_result(
                    "Veo varios tickets. ¿Cuál quieres que revise? Puedes indicar "
                    "el primero, el segundo, el asunto o el código de seguimiento.",
                    ChatbotSuggestedAction.CLARIFY_TICKET_CODE,
                    payload,
                    resolved=False,
                    requires_clarification=True,
                )
                result["ticket_reference_codes"] = [
                    item.tracking_code for item in owned_tickets.items
                ]
                result["pending_question"] = "¿Cuál de tus tickets quieres que revise?"
                if request.query_type in {
                    ChatbotTicketQueryType.TICKET_STATUS,
                    ChatbotTicketQueryType.ADVISOR_RESPONSE_STATUS,
                    ChatbotTicketQueryType.ADVISOR_REPLY,
                }:
                    result["pending_ticket_query"] = {
                        "action": request.action.value,
                        "query_type": request.query_type.value,
                    }
                return result
        else:
            ticket = self._find_ticket_for_query(session, current_user, request)
        if ticket is None:
            answer = (
                "No encontré un ticket disponible con ese código. Verifica el "
                "código o revisa tus tickets."
                if request.tracking_code is not None
                else "No tienes tickets registrados para revisar."
            )
            payload = ChatbotTicketQuery(
                query_type=request.query_type,
                found=False,
            )
            return self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )

        card = self._ticket_card(ticket)
        if request.query_type == ChatbotTicketQueryType.TICKET_STATUS:
            payload = ChatbotTicketQuery(
                query_type=request.query_type,
                found=True,
                ticket=card,
            )
            requested_fields = request.requested_fields or frozenset(
                {"CODE", "SUBJECT", "STATUS", "ASSIGNMENT"}
            )
            answer_parts = []
            if "SUBJECT" in requested_fields:
                answer_parts.append(f"El asunto es {ticket.subject}.")
            if "STATUS" in requested_fields:
                answer_parts.append(
                    f"El ticket {ticket.tracking_code} está en estado "
                    f"{card.status_label}."
                )
            if "ASSIGNMENT" in requested_fields or "STATUS" in requested_fields:
                answer_parts.append(self._ticket_assignment_message(ticket))
            answer = " ".join(answer_parts)
            result = self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )
            result["ticket_reference_codes"] = [ticket.tracking_code]
            return result

        advisor_comments = [
            comment
            for comment in self.tickets.comments(session, ticket.id, current_user)
            if comment.author_role == "ASESOR"
        ]
        latest_comment = max(
            advisor_comments,
            key=lambda comment: (comment.created_at, comment.id),
            default=None,
        )
        replied = latest_comment is not None
        latest_response = (
            ChatbotAdvisorResponse(
                author_name=latest_comment.author_name,
                created_at=latest_comment.created_at,
                content=(
                    latest_comment.content
                    if request.query_type == ChatbotTicketQueryType.ADVISOR_REPLY
                    else None
                ),
            )
            if latest_comment is not None
            else None
        )
        payload = ChatbotTicketQuery(
            query_type=request.query_type,
            found=True,
            ticket=card,
            advisor_responded=replied,
            latest_advisor_response=latest_response,
        )
        if request.query_type == ChatbotTicketQueryType.ADVISOR_REPLY:
            answer = (
                f"La respuesta más reciente de {latest_comment.author_name} "
                f"({latest_comment.created_at.isoformat()}): {latest_comment.content}"
                if latest_comment is not None
                else "Todavía no hay una respuesta visible de un asesor en este ticket."
            )
        else:
            answer = (
                f"Sí. {latest_comment.author_name} respondió el "
                f"{latest_comment.created_at.isoformat()}."
                if latest_comment is not None
                else "Todavía no hay una respuesta visible de un asesor en este ticket."
            )
        return self._ticket_query_result(answer, request.action, payload, resolved=True)

    def _find_ticket_for_query(
        self,
        session: Session,
        current_user: AuthenticatedUser,
        request: TicketQueryRequest,
    ) -> TicketRead | None:
        if request.tracking_code is not None:
            try:
                return self.tickets.get_by_tracking_code(
                    session, request.tracking_code, current_user
                )
            except HTTPException as error:
                if error.status_code in {
                    status.HTTP_403_FORBIDDEN,
                    status.HTTP_404_NOT_FOUND,
                }:
                    return None
                raise
        latest_page = self.tickets.list_mine(session, current_user, page=1, page_size=1)
        return latest_page.items[0] if latest_page.items else None

    @staticmethod
    def _ticket_card(ticket: TicketRead) -> ChatbotTicketCard:
        return ChatbotTicketCard(
            tracking_code=ticket.tracking_code,
            subject=ticket.subject,
            status=ticket.status,
            status_label=_TICKET_STATUS_LABELS[ticket.status],
            priority=ticket.priority,
            priority_label=_TICKET_PRIORITY_LABELS[ticket.priority],
            updated_at=ticket.updated_at,
            assigned_advisor_name=ticket.assigned_advisor_name,
            assignment_status=(
                ChatbotTicketAssignmentStatus.ASSIGNED
                if ticket.assigned_advisor_id is not None
                else ChatbotTicketAssignmentStatus.UNASSIGNED
            ),
        )

    @staticmethod
    def _status_filter_value(request: TicketQueryRequest) -> str:
        if request.statuses is None:
            return "ALL"
        if request.statuses == _OPEN_TICKET_STATUSES:
            return "OPEN"
        if request.statuses == frozenset({TicketStatus.PENDIENTE_CLIENTE}):
            return TicketStatus.PENDIENTE_CLIENTE.value
        return next(iter(request.statuses)).value

    @staticmethod
    def _ticket_assignment_message(ticket: TicketRead) -> str:
        if ticket.assigned_advisor_id is not None:
            if ticket.assigned_advisor_name:
                return f"Está asignado a {ticket.assigned_advisor_name}."
            return (
                "El sistema confirma que ya tiene un asesor asignado, aunque no "
                "puedo mostrar su nombre en este momento."
            )
        if ticket.status == TicketStatus.NUEVO:
            return "Todavía está pendiente de asignación; ningún asesor lo ha tomado."
        return "El sistema no reporta un asesor asignado actualmente."

    @staticmethod
    def _ticket_query_result(
        answer: str,
        action: ChatbotSuggestedAction,
        ticket_query: ChatbotTicketQuery,
        *,
        resolved: bool,
        requires_clarification: bool = False,
    ) -> dict[str, object]:
        return {
            "answer": answer,
            "intent": ticket_query.query_type.value,
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": resolved,
            "offers_ticket": False,
            "requires_clarification": requires_clarification,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": action,
            "ticket_query": ticket_query,
        }

    @staticmethod
    def _ticket_authentication_response(
        conversation: Conversation,
        *,
        for_advisor: bool = False,
    ) -> dict[str, object]:
        if for_advisor:
            answer = (
                "Puedo ayudarte a dejar una solicitud para que la atienda un asesor. "
                "Inicia sesión o regístrate para preparar el ticket; todavía no se "
                "ha creado una solicitud ni se ha asignado un asesor. Al volver, "
                "podrás retomar esta conversación. Nunca compartas contraseñas, "
                "PIN, CVV, OTP, tokens ni códigos de seguridad."
            )
            intent = "ADVISOR_REQUEST_AUTHENTICATION_REQUIRED"
        else:
            answer = (
                "Para preparar y confirmar un ticket, inicia sesión o regístrate. "
                "Después podrás vincular y retomar esta conversación. No compartas "
                "contraseñas, OTP, CVV, tokens ni códigos de seguridad."
            )
            intent = "TICKET_CREATION_AUTHENTICATION_REQUIRED"
        return {
            "answer": answer,
            "intent": intent,
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": False,
            "offers_ticket": False,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": ChatbotSuggestedAction.AUTHENTICATION_REQUIRED,
            "advisor_handoff_requested": for_advisor,
            "ticket_query": ChatbotTicketQuery(
                query_type=ChatbotTicketQueryType.AUTHENTICATION_REQUIRED,
                authentication_required=True,
                authentication_actions=[
                    ChatbotAuthenticationAction.LOGIN,
                    ChatbotAuthenticationAction.REGISTER,
                ],
            ),
        }

    @staticmethod
    def _ticket_draft_response(
        conversation: Conversation,
        reply: TicketDraftReply,
        *,
        advisor_handoff: bool = False,
    ) -> dict[str, object]:
        if reply.cancelled:
            intent = "TICKET_DRAFT_CANCELLED"
            action = ChatbotSuggestedAction.NONE
        elif reply.ready:
            intent = "TICKET_DRAFT_READY"
            action = (
                ChatbotSuggestedAction.CONVERT_TO_TICKET
                if reply.draft.status == ChatbotTicketDraftStatus.CONFIRMED
                else ChatbotSuggestedAction.CONFIRM_TICKET
            )
        else:
            intent = "TICKET_DRAFT"
            action = ChatbotSuggestedAction.ASK_CLARIFICATION
        answer = reply.answer
        if advisor_handoff and not reply.cancelled:
            intent = "ADVISOR_TICKET_DRAFT"
            answer = (
                "No hay transferencia en vivo. Para que un asesor atienda tu caso, "
                "prepararemos una solicitud que entrará a la cola después de que "
                "revises y confirmes los datos; entonces recibirás un código de "
                "seguimiento. "
                f"{reply.answer}"
            )
        return {
            "answer": answer,
            "intent": intent,
            "faq_id": conversation.last_faq_id,
            "category_name": None,
            "confidence": 1.0,
            "resolved": False,
            "offers_ticket": reply.offers_ticket,
            "requires_clarification": reply.requires_clarification,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": action,
            "advisor_handoff_requested": advisor_handoff and not reply.cancelled,
            "ticket_query": None,
            "counts_as_clarification": reply.requires_clarification,
        }

    @staticmethod
    def _advisor_handoff_unavailable_response() -> dict[str, object]:
        return {
            "answer": (
                "La solicitud de atención de un asesor no está disponible "
                "temporalmente. Puedes iniciar una solicitud para darle seguimiento."
            ),
            "intent": "ADVISOR_REQUEST_UNAVAILABLE",
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": False,
            "offers_ticket": True,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": ChatbotSuggestedAction.CREATE_TICKET,
            "advisor_handoff_requested": False,
            "counts_as_clarification": False,
        }

    @staticmethod
    def _unhelpful_follow_up_response(
        conversation: Conversation,
        *,
        exhausted: bool = False,
    ) -> dict[str, object]:
        if exhausted:
            return {
                "answer": (
                    "Siento que aún no haya quedado claro. Puedes explorar las "
                    "categorías de preguntas frecuentes, solicitar atención de un "
                    "asesor o iniciar un ticket cuando decidas confirmarlo."
                ),
                "intent": "FOLLOW_UP_UNRESOLVED",
                "faq_id": conversation.last_faq_id,
                "category_name": None,
                "confidence": conversation.last_confidence or 0.0,
                "resolved": False,
                "offers_ticket": False,
                "requires_clarification": False,
                "options": [],
                "candidate_faq_ids": [],
                "fallback_reason": None,
                "escalated": False,
                "match": None,
                "response_source": "DETERMINISTIC",
                "preserve_context": True,
                "clear_clarification_attempts": True,
                "available_actions": [
                    ChatbotAvailableAction.BROWSE_KNOWLEDGE,
                    ChatbotAvailableAction.CREATE_TICKET,
                    ChatbotAvailableAction.CONTACT_ADVISOR,
                ],
            }
        return {
            "answer": (
                "Entiendo. Mi respuesta anterior no resolvió lo que necesitas. "
                "¿Qué parte sigue pendiente o qué resultado esperabas?"
            ),
            "intent": "FOLLOW_UP_UNRESOLVED",
            "faq_id": conversation.last_faq_id,
            "category_name": None,
            "confidence": conversation.last_confidence or 0.0,
            "resolved": False,
            "offers_ticket": False,
            "requires_clarification": True,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": ChatbotSuggestedAction.ASK_CLARIFICATION,
        }

    @staticmethod
    def _knowledge_unavailable_response() -> dict[str, object]:
        return {
            "answer": persona_text(
                "error",
                enabled=get_settings().chatbot_persona_enabled,
                fallback=(
                    "No puedo consultar la información de ayuda en este momento. "
                    "Inténtalo de nuevo más tarde."
                ),
            ),
            "intent": "KNOWLEDGE_UNAVAILABLE",
            "faq_id": None,
            "category_name": None,
            "confidence": 0.0,
            "resolved": False,
            "offers_ticket": False,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": "KNOWLEDGE_UNAVAILABLE",
            "escalated": False,
            "match": None,
            "response_source": "FALLBACK",
            "suggested_action": ChatbotSuggestedAction.NONE,
        }

    @staticmethod
    def _contextual_provider_input(
        previous_user_message: str,
        previous_bot_message: str,
        current_message: str,
    ) -> str:
        limit = get_settings().chatbot_max_message_length
        policy = "Contexto reciente solo para resolver la referencia actual.\n"
        labels = (
            "Mensaje previo del cliente: ",
            "Respuesta previa del asistente: ",
            "Consulta actual: ",
        )
        remaining = max(0, limit - len(policy) - sum(map(len, labels)) - 2)
        previous_budget = remaining // 5
        assistant_budget = remaining // 5
        current_budget = remaining - previous_budget - assistant_budget

        def safe_public_text(value: str) -> str:
            without_ticket_codes = _PROVIDER_TICKET_REFERENCE_PATTERN.sub(
                "[código de solicitud omitido]", value
            )
            return minimize_provider_input(without_ticket_codes)

        value = (
            f"{policy}{labels[0]}"
            f"{safe_public_text(previous_user_message)[:previous_budget]}\n"
            f"{labels[1]}"
            f"{safe_public_text(previous_bot_message)[:assistant_budget]}\n"
            f"{labels[2]}{safe_public_text(current_message)[:current_budget]}"
        )
        return value[:limit]

    @staticmethod
    def _suggested_action(result: dict[str, object]) -> ChatbotSuggestedAction:
        if result.get("escalated"):
            return ChatbotSuggestedAction.CONTACT_ADVISOR
        if result.get("requires_clarification"):
            return ChatbotSuggestedAction.ASK_CLARIFICATION
        if result.get("offers_ticket"):
            return ChatbotSuggestedAction.CREATE_TICKET
        return ChatbotSuggestedAction.NONE

    @staticmethod
    def _conversation_lifecycle_action(
        content: str,
    ) -> ChatbotSuggestedAction | None:
        normalized = normalize_text(content).strip(" .,!?:;¿¡")
        if normalized in _CONVERSATION_MENU_COMMANDS:
            return ChatbotSuggestedAction.SHOW_MENU
        if normalized in _END_CONVERSATION_COMMANDS:
            return ChatbotSuggestedAction.END_CONVERSATION
        return None

    @staticmethod
    def _conversation_lifecycle_response(
        action: ChatbotSuggestedAction,
        *,
        draft_paused: bool,
        draft_cancelled: bool,
    ) -> dict[str, object]:
        if action == ChatbotSuggestedAction.SHOW_MENU:
            answer = persona_text(
                "menu_return",
                enabled=get_settings().chatbot_persona_enabled,
                fallback=(
                    "Volví al menú. Elige un tema o escribe otra consulta; "
                    "el historial de esta conversación se conserva."
                ),
            )
            if draft_paused:
                answer += (
                    " Tu borrador de ticket quedó guardado; puedes retomarlo "
                    "cuando quieras."
                )
            intent = "CONVERSATION_MENU"
        else:
            answer = persona_text(
                "farewell",
                enabled=get_settings().chatbot_persona_enabled,
                fallback=(
                    "La conversación finalizó y su historial se conserva. "
                    "No se creó ningún ticket."
                ),
            )
            if draft_cancelled:
                answer += " El borrador pendiente se canceló."
            intent = "CONVERSATION_ENDED"
        result = ChatbotService._greeting_response()
        result.update(
            answer=answer,
            intent=intent,
            resolved=True,
            offers_ticket=False,
            requires_clarification=False,
            options=[],
            fallback_reason=None,
            escalated=False,
            match=None,
            response_source="DETERMINISTIC",
            preserve_context=False,
            suggested_action=action,
            available_actions=[],
            sources=[],
        )
        return result

    def _main_menu_ui(self, session: Session, *, draft_paused: bool) -> ChatbotUI:
        options: list[ChatbotUIOption] = []
        if self._available_faq_categories(session):
            options.append(
                ChatbotUIOption(
                    id="menu:faq",
                    label="Preguntas frecuentes",
                    action=ChatbotUIAction.SHOW_FAQ_DISCOVERY,
                )
            )
        options.extend(
            [
                ChatbotUIOption(
                    id="menu:ticket_status",
                    label="Estado de mi caso",
                    action=ChatbotUIAction.CHECK_TICKET_STATUS,
                ),
                ChatbotUIOption(
                    id="menu:tickets",
                    label="Mis solicitudes",
                    action=ChatbotUIAction.LIST_MY_TICKETS,
                ),
                ChatbotUIOption(
                    id="menu:create_ticket",
                    label="Registrar una solicitud",
                    action=ChatbotUIAction.CREATE_TICKET,
                ),
                ChatbotUIOption(
                    id="menu:advisor",
                    label="Solicitar atención de un asesor",
                    action=ChatbotUIAction.CONTACT_ADVISOR,
                ),
            ]
        )
        if draft_paused:
            options.append(
                ChatbotUIOption(
                    id="menu:resume_ticket_draft",
                    label="Retomar borrador de solicitud",
                    action=ChatbotUIAction.RESUME_TICKET_DRAFT,
                )
            )
        options.append(
            ChatbotUIOption(
                id="menu:end",
                label="Finalizar conversación",
                action=ChatbotUIAction.END_CONVERSATION,
            )
        )
        return ChatbotUI(type=ChatbotUIType.MAIN_MENU, menu_id="main", options=options)

    @staticmethod
    def _faq_choices_ui(
        faq_ids: list[str], labels: list[str], *, menu_id: str
    ) -> ChatbotUI | None:
        options = [
            ChatbotUIOption(
                id=f"faq:{faq_id}",
                label=label,
                action=ChatbotUIAction.SELECT_FAQ,
            )
            for faq_id, label in zip(faq_ids, labels, strict=False)
            if faq_id and label
        ][:18]
        if menu_id == "faq_questions":
            options.extend(
                [
                    ChatbotUIOption(
                        id="nav:back",
                        label="Volver a temas",
                        action=ChatbotUIAction.NAVIGATE_BACK,
                    ),
                    ChatbotUIOption(
                        id="nav:menu",
                        label="Volver al menú",
                        action=ChatbotUIAction.SHOW_MENU,
                    ),
                ]
            )
        if not options:
            return None
        return ChatbotUI(
            type=ChatbotUIType.FAQ_CHOICES,
            menu_id=menu_id,
            options=options,
        )

    @staticmethod
    def _faq_category_choices_ui(
        category_ids: list[str], labels: list[str]
    ) -> ChatbotUI | None:
        options = [
            ChatbotUIOption(
                id=f"category:{category_id}",
                label=label,
                action=ChatbotUIAction.SHOW_FAQ_CATEGORY,
            )
            for category_id, label in zip(category_ids, labels, strict=False)
            if category_id and label
        ][:19]
        if not options:
            return None
        options.append(
            ChatbotUIOption(
                id="nav:menu",
                label="Volver al menú",
                action=ChatbotUIAction.SHOW_MENU,
            )
        )
        return ChatbotUI(
            type=ChatbotUIType.FAQ_CATEGORY_CHOICES,
            menu_id="faq_categories",
            options=options,
        )

    @staticmethod
    def _faq_subcategory_choices_ui(
        subcategories: list[dict[str, object]],
    ) -> ChatbotUI | None:
        options = [
            ChatbotUIOption(
                id=str(item["id"]),
                label=str(item["label"]),
                action=ChatbotUIAction.SHOW_FAQ_SUBCATEGORY,
            )
            for item in subcategories
            if isinstance(item.get("id"), str) and isinstance(item.get("label"), str)
        ][:18]
        if not options:
            return None
        options.extend(
            [
                ChatbotUIOption(
                    id="nav:back",
                    label="Volver a categorías",
                    action=ChatbotUIAction.NAVIGATE_BACK,
                ),
                ChatbotUIOption(
                    id="nav:menu",
                    label="Volver al menú",
                    action=ChatbotUIAction.SHOW_MENU,
                ),
            ]
        )
        return ChatbotUI(
            type=ChatbotUIType.FAQ_SUBCATEGORY_CHOICES,
            menu_id="faq_subcategories",
            options=options,
        )

    def _ui_for_result(
        self, session: Session, result: dict[str, object], context: dict[str, object]
    ) -> ChatbotUI | None:
        if result.get("suggested_action") == ChatbotSuggestedAction.SHOW_MENU:
            return self._main_menu_ui(
                session, draft_paused=context.get("ticket_draft_paused") is True
            )
        subcategories = result.get("candidate_faq_subcategories")
        if (
            result.get("intent") == "FAQ_SUBCATEGORY_DISCOVERY"
            and isinstance(subcategories, list)
            and all(isinstance(value, dict) for value in subcategories)
        ):
            return self._faq_subcategory_choices_ui(subcategories)
        category_ids = result.get("candidate_faq_category_ids")
        category_labels = result.get("options")
        if (
            result.get("intent")
            in {"FAQ_CATEGORY_DISCOVERY", ChatbotIntent.OUT_OF_SCOPE.value}
            and isinstance(category_ids, list)
            and isinstance(category_labels, list)
            and all(isinstance(value, str) for value in category_ids)
            and all(isinstance(value, str) for value in category_labels)
        ):
            return self._faq_category_choices_ui(category_ids, category_labels)
        faq_ids = result.get("candidate_faq_ids")
        labels = result.get("options")
        if (
            isinstance(faq_ids, list)
            and isinstance(labels, list)
            and all(isinstance(value, str) for value in faq_ids)
            and all(isinstance(value, str) for value in labels)
        ):
            return self._faq_choices_ui(
                faq_ids,
                labels,
                menu_id=(
                    "faq_questions"
                    if result.get("candidate_faq_subcategory_id")
                    else "faq_discovery"
                    if result.get("intent") == "FAQ_DISCOVERY"
                    else "faq_suggestions"
                ),
            )
        return None

    def _ui_for_conversation(
        self, session: Session, context: dict[str, object]
    ) -> ChatbotUI | None:
        if context.get("menu_open") is True:
            return self._main_menu_ui(
                session, draft_paused=context.get("ticket_draft_paused") is True
            )
        menu_id = context.get("pending_ui_menu_id")
        if menu_id == "faq_subcategories":
            category_id = context.get("pending_faq_category_id")
            if isinstance(category_id, str):
                result, _ = self._faq_subcategory_discovery_response(
                    session, category_id=category_id
                )
                subcategories = result.get("candidate_faq_subcategories")
                if isinstance(subcategories, list):
                    return self._faq_subcategory_choices_ui(subcategories)
        if menu_id == "faq_questions":
            raw_ids = context.get("pending_faq_ids")
            if isinstance(raw_ids, list):
                active = {
                    faq.id: faq
                    for faq in self._available_public_faqs(session)
                    if faq.id in raw_ids
                }
                faq_ids = [faq_id for faq_id in raw_ids if faq_id in active]
                labels = [
                    active[faq_id].title or active[faq_id].question
                    for faq_id in faq_ids
                ]
                return self._faq_choices_ui(faq_ids, labels, menu_id="faq_questions")
        raw_category_ids = context.get("pending_faq_category_ids")
        if isinstance(raw_category_ids, list):
            allowed_category_ids = {
                value for value in raw_category_ids if isinstance(value, str)
            }
            categories = [
                (category_id, label)
                for category_id, label in self._available_faq_categories(session)
                if category_id in allowed_category_ids
            ]
            if categories:
                return self._faq_category_choices_ui(
                    [category_id for category_id, _ in categories],
                    [label for _, label in categories],
                )
        raw_ids = context.get("pending_faq_ids")
        if not isinstance(raw_ids, list):
            return None
        allowed_ids = {value for value in raw_ids if isinstance(value, str)}
        if not allowed_ids:
            return None
        active = {
            faq.id: faq
            for faq in self.faqs.list_active(session)
            if faq.id in allowed_ids and self._is_publicly_available_faq(faq)
        }
        faq_ids = [faq_id for faq_id in raw_ids if faq_id in active]
        labels = [active[faq_id].title or active[faq_id].question for faq_id in faq_ids]
        menu_id = context.get("pending_ui_menu_id")
        if menu_id not in {"faq_discovery", "faq_suggestions"}:
            menu_id = "faq_discovery"
        return self._faq_choices_ui(faq_ids, labels, menu_id=menu_id)

    def _resolve_faq_navigation_target(
        self, conversation: Conversation, option_id: str
    ) -> str:
        context = conversation.context_json or {}
        menu_id = context.get("pending_ui_menu_id")
        if conversation.status != ConversationStatus.WAITING_CLARIFICATION:
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        if option_id == "nav:menu" and menu_id in {
            "faq_categories",
            "faq_subcategories",
            "faq_questions",
        }:
            return "main"
        if option_id == "nav:back":
            targets = {
                "faq_categories": "main",
                "faq_subcategories": "faq_categories",
                "faq_questions": "faq_subcategories",
            }
            target = targets.get(str(menu_id))
            if target is not None:
                if target == "faq_subcategories" and not isinstance(
                    context.get("pending_faq_category_id"), str
                ):
                    target = None
                if target is not None:
                    return target
        raise HTTPException(
            status_code=409,
            detail="La opción ya no está disponible en esta conversación",
        )

    def _resolve_faq_subcategory_option(
        self, session: Session, conversation: Conversation, option_id: str
    ) -> dict[str, object]:
        context = conversation.context_json or {}
        raw_subcategories = context.get("pending_faq_subcategories")
        if (
            conversation.status != ConversationStatus.WAITING_CLARIFICATION
            or context.get("pending_ui_menu_id") != "faq_subcategories"
            or not isinstance(raw_subcategories, list)
        ):
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        offered = next(
            (
                value
                for value in raw_subcategories
                if isinstance(value, dict) and value.get("id") == option_id
            ),
            None,
        )
        if not isinstance(offered, dict):
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        category_id = offered.get("category_id")
        if not isinstance(category_id, str):
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        result, _ = self._faq_subcategory_discovery_response(
            session, category_id=category_id
        )
        current = result.get("candidate_faq_subcategories")
        if not isinstance(current, list):
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        resolved = next(
            (
                value
                for value in current
                if isinstance(value, dict) and value.get("id") == option_id
            ),
            None,
        )
        if not isinstance(resolved, dict):
            raise HTTPException(
                status_code=409,
                detail="La opción ya no está disponible en esta conversación",
            )
        return resolved

    def _resolve_option_id(
        self, session: Session, conversation: Conversation, option_id: str
    ) -> str:
        context = conversation.context_json or {}
        if option_id.startswith("menu:"):
            if context.get("menu_open") is not True:
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            actions = {
                "menu:ticket_status": "Quiero saber el estado de mi ticket",
                "menu:tickets": "Mostrar mis tickets",
                "menu:create_ticket": "Quiero crear un ticket",
                "menu:advisor": "Quiero hablar con un asesor",
                "menu:end": "Finalizar conversación",
            }
            if option_id == "menu:faq":
                if not self._available_faq_categories(session):
                    raise HTTPException(
                        status_code=409,
                        detail="La opción ya no está disponible en esta conversación",
                    )
                return "Preguntas frecuentes"
            if option_id == "menu:resume_ticket_draft":
                if context.get("ticket_draft_paused") is not True:
                    raise HTTPException(
                        status_code=409,
                        detail="La opción ya no está disponible en esta conversación",
                    )
                return "Retomar mi borrador de ticket"
            content = actions.get(option_id)
            if content is None:
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            return content
        if option_id.startswith("faq:"):
            faq_id = option_id.removeprefix("faq:")
            raw_ids = context.get("pending_faq_ids")
            if (
                not isinstance(raw_ids, list)
                or faq_id not in raw_ids
                or conversation.status != ConversationStatus.WAITING_CLARIFICATION
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            faq = next(
                (
                    item
                    for item in self.faqs.list_active(session)
                    if item.id == faq_id and self._is_publicly_available_faq(item)
                ),
                None,
            )
            if faq is None:
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            return faq.title or faq.question
        if option_id.startswith("category:"):
            category_id = option_id.removeprefix("category:")
            raw_ids = context.get("pending_faq_category_ids")
            if (
                not isinstance(raw_ids, list)
                or category_id not in raw_ids
                or conversation.status != ConversationStatus.WAITING_CLARIFICATION
            ):
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            category = next(
                (
                    (item_id, label)
                    for item_id, label in self._available_faq_categories(session)
                    if item_id == category_id
                ),
                None,
            )
            if category is None:
                raise HTTPException(
                    status_code=409,
                    detail="La opción ya no está disponible en esta conversación",
                )
            return category[1]
        raise HTTPException(
            status_code=409,
            detail="La opción ya no está disponible en esta conversación",
        )

    def _read_ticket_draft(
        self, session: Session, context: dict[str, object]
    ) -> ChatbotTicketDraft:
        draft = self.ticket_drafts.read(session, context.get(TICKET_DRAFT_CONTEXT_KEY))
        return draft.model_copy(
            update={"paused": context.get("ticket_draft_paused") is True}
        )

    @staticmethod
    def _greeting_response() -> dict[str, object]:
        return {
            "answer": persona_text(
                "greeting",
                enabled=get_settings().chatbot_persona_enabled,
                fallback=(
                    "Hola. Soy el asistente virtual del prototipo académico de "
                    "gestión de tickets de Banco GNB Perú. Puedo orientarte sobre "
                    "preguntas frecuentes o el seguimiento de una solicitud."
                ),
            ),
            "intent": ChatbotIntent.GREETING.value,
            "faq_id": None,
            "category_name": None,
            "confidence": 1.0,
            "resolved": True,
            "offers_ticket": False,
            "requires_clarification": False,
            "options": [],
            "fallback_reason": None,
            "escalated": False,
            "match": None,
            "response_source": "DETERMINISTIC",
            "suggested_action": ChatbotSuggestedAction.SHOW_MENU,
        }

    @staticmethod
    def _public_sources(
        matches: list[FAQMatch] | tuple[object, ...],
    ) -> list[dict[str, object]]:
        sources: list[dict[str, object]] = []
        for item in matches:
            if isinstance(item, FAQMatch):
                if not has_verified_official_source(
                    item.faq.source_url, item.faq.source_verified_at
                ):
                    continue
                sources.append(
                    {
                        "title": item.faq.title or item.faq.question,
                        "category": item.category_name,
                        "url": item.faq.source_url,
                        "verified_at": item.faq.source_verified_at,
                    }
                )
            else:
                if not getattr(item, "source_url", None) or not getattr(
                    item, "source_verified_at", None
                ):
                    continue
                sources.append(
                    {
                        "title": item.title,
                        "category": item.category,
                        "url": getattr(item, "source_url", None),
                        "verified_at": getattr(item, "source_verified_at", None),
                    }
                )
        return sources

    def _enforce_limits(self, session: Session, conversation: Conversation) -> None:
        settings = get_settings()
        if conversation.turn_count >= settings.chatbot_max_turns:
            raise HTTPException(
                status_code=429, detail="Se alcanzó el límite de turnos"
            )
        since = datetime.now(UTC) - timedelta(minutes=1)
        if (
            self.conversations.count_recent_messages(session, conversation.id, since)
            >= settings.chatbot_messages_per_minute
        ):
            raise HTTPException(
                status_code=429, detail="Se alcanzó el límite de mensajes por minuto"
            )

    @staticmethod
    def _ensure_message_allowed(conversation: Conversation) -> None:
        if conversation.status in {
            ConversationStatus.CONVERTED_TO_TICKET,
            ConversationStatus.CLOSED,
            ConversationStatus.EXPIRED,
        }:
            raise HTTPException(
                status_code=409, detail="La conversación no está activa"
            )

    @staticmethod
    def _clean_content(content: str) -> str:
        clean = content.strip()
        if not clean or _MARKUP_PATTERN.search(clean):
            raise HTTPException(status_code=422, detail="El mensaje no es válido")
        if len(clean) > get_settings().chatbot_max_message_length:
            raise HTTPException(
                status_code=422, detail="El mensaje supera el tamaño permitido"
            )
        return clean

    @staticmethod
    def _anonymous_key(client_ip: str | None) -> str:
        value = client_ip or "unknown"
        return hmac.new(
            get_settings().secret_key.encode(),
            value.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _require_public_actor(current_user: AuthenticatedUser | None) -> None:
        if current_user is not None and current_user.role != "CLIENTE":
            raise HTTPException(
                status_code=403,
                detail="El chatbot está disponible para visitantes y clientes",
            )

    def _authorized_conversation(
        self,
        session: Session,
        conversation_id: str,
        current_user: AuthenticatedUser | None,
    ) -> Conversation:
        conversation = self.conversations.get_conversation(session, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversación no encontrada")
        self._require_public_actor(current_user)
        if conversation.user_id is None:
            return conversation
        if current_user is None:
            raise HTTPException(status_code=401, detail="Autenticación requerida")
        if conversation.user_id != current_user.user.id:
            raise HTTPException(status_code=403, detail="Conversación no autorizada")
        return conversation

    def _conversation_read(
        self,
        session: Session,
        conversation: Conversation,
        current_user: AuthenticatedUser | None = None,
    ) -> ConversationRead:
        context = conversation.context_json or {}
        ticket = self.tickets.repository.get_by_conversation(session, conversation.id)
        return ConversationRead(
            id=conversation.id,
            user_id=conversation.user_id,
            status=conversation.status,
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
            detected_intent=conversation.detected_intent,
            last_faq_id=conversation.last_faq_id,
            category_id=conversation.category_id,
            pending_question=conversation.pending_question,
            turn_count=conversation.turn_count,
            last_confidence=conversation.last_confidence,
            escalation_reason=conversation.escalation_reason,
            escalated_at=conversation.escalated_at,
            converted_at=conversation.converted_at,
            last_activity_at=conversation.last_activity_at,
            ticket_draft=self._read_ticket_draft(session, context),
            menu_open=context.get("menu_open") is True,
            ui=self._ui_for_conversation(session, context),
            converted_ticket=(
                ChatbotConvertedTicketSummary(
                    id=ticket.id,
                    tracking_code=ticket.tracking_code,
                    subject=ticket.subject,
                    status=ticket.status,
                )
                if ticket is not None
                else None
            ),
            proactive_notices=self._proactive_notices(session, current_user),
            messages=[
                self._message_read(message)
                for message in self.conversations.list_messages(
                    session, conversation.id
                )
            ],
        )

    @staticmethod
    def _message_read(message: ChatMessage) -> ChatMessageRead:
        return ChatMessageRead(
            id=message.id,
            sender_type=message.sender_type,
            content=message.content,
            intent=message.intent,
            confidence=float(message.confidence)
            if message.confidence is not None
            else None,
            faq_id=message.faq_id,
            intent_classification=ChatbotService._message_ai_intent(message),
            response_source=message.response_source,
            created_at=message.created_at,
        )

    @staticmethod
    def _conversation_summary(
        messages: list[ChatMessage], current_message: str
    ) -> str | None:
        def relevant_detail(content: str) -> str | None:
            clean_content = " ".join(minimize_provider_input(content).split())
            labeled = re.search(
                r"\b(?:motivo|problema)\s*[:=]\s*(.+)$",
                clean_content,
                re.IGNORECASE,
            )
            if labeled is not None:
                detail = labeled.group(1).strip(" .,:;")
                return detail[:300] if len(detail) >= 5 else None

            description = TicketDraftService._labeled_values(clean_content).get(
                "description"
            )
            if description and len(description) >= 5:
                return description[:300]

            incident = TicketDraftService._incident_text(clean_content)
            if incident:
                return incident[:300]

            if ChatbotService._is_advisor_request(clean_content):
                reason = re.search(
                    r"\b(?:debido\s+a|porque|por|sobre)\s+(.+)$",
                    clean_content,
                    re.IGNORECASE,
                )
                if reason is not None:
                    detail = reason.group(1).strip(" .,:;")
                    if detail.casefold().startswith("favor "):
                        return None
                    return detail[:300] if len(detail) >= 5 else None
            return None

        current_detail = relevant_detail(current_message)
        candidates = [current_detail] if current_detail else []
        if not candidates:
            for index, message in enumerate(messages):
                if message.sender_type != SenderType.USER:
                    continue
                following_bot = next(
                    (
                        item
                        for item in messages[index + 1 :]
                        if item.sender_type == SenderType.BOT
                    ),
                    None,
                )
                if following_bot is None or following_bot.intent not in {
                    "TICKET_DRAFT",
                    "ADVISOR_TICKET_DRAFT",
                }:
                    continue
                if (
                    not re.search(
                        r"\b(?:describe|descripcion|problema|incidente)\b",
                        normalize_text(following_bot.content),
                    )
                    and not re.search(
                        r"\b(?:descripcion|detalle|motivo|problema)\s*[:=]",
                        message.content,
                        re.IGNORECASE,
                    )
                    and TicketDraftService._incident_text(message.content) is None
                ):
                    continue
                detail = relevant_detail(message.content)
                if detail:
                    candidates.append(detail)

        distinct_candidates = list(dict.fromkeys(candidates[-3:]))
        if not distinct_candidates:
            return None
        context = " / ".join(distinct_candidates)
        return f"Resumen del problema (extracto determinístico): {context}"[:1200]

    def _proactive_notices(
        self,
        session: Session,
        current_user: AuthenticatedUser | None,
    ) -> list[ChatbotProactiveNotice]:
        settings = get_settings()
        if (
            not settings.chatbot_proactive_notices_enabled
            or current_user is None
            or current_user.role != "CLIENTE"
        ):
            return []
        page = self.tickets.list_mine(
            session,
            current_user,
            page=1,
            page_size=20,
            statuses={TicketStatus.PENDIENTE_CLIENTE, TicketStatus.RESUELTO},
        )
        notices: list[ChatbotProactiveNotice] = []
        for ticket in page.items:
            if ticket.status == TicketStatus.PENDIENTE_CLIENTE:
                notices.append(
                    ChatbotProactiveNotice(
                        type=ChatbotProactiveNoticeType.CLIENT_ACTION_REQUIRED,
                        ticket_id=ticket.id,
                        tracking_code=ticket.tracking_code,
                        message=(
                            f"Tu ticket {ticket.tracking_code} espera "
                            "una respuesta tuya."
                        ),
                    )
                )
            elif ticket.status == TicketStatus.RESUELTO:
                notices.append(
                    ChatbotProactiveNotice(
                        type=ChatbotProactiveNoticeType.RESOLVED_CONFIRMATION_REQUIRED,
                        ticket_id=ticket.id,
                        tracking_code=ticket.tracking_code,
                        message=(
                            f"Tu ticket {ticket.tracking_code} fue resuelto. "
                            "¿Confirmas el cierre de esta solicitud?"
                        ),
                    )
                )
        return notices

    @staticmethod
    def _quick_replies(
        draft: ChatbotTicketDraft,
        result: dict[str, object],
    ) -> list[ChatbotQuickReply]:
        if not get_settings().chatbot_quick_replies_enabled:
            return []
        if isinstance(result.get("ticket_query"), ChatbotTicketQuery):
            return []
        if draft.status == ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION:
            return [
                ChatbotQuickReply(
                    id="draft-confirm",
                    label="Confirmar solicitud",
                    content="Confirmo",
                ),
                ChatbotQuickReply(
                    id="draft-cancel",
                    label="Cancelar borrador",
                    content="Cancelo el borrador",
                ),
            ]
        if draft.status == ChatbotTicketDraftStatus.CONFIRMED:
            return []
        options = result.get("options")
        if result.get("requires_clarification") and isinstance(options, list):
            replies = [
                ChatbotQuickReply(
                    id=f"clarification-{index + 1}",
                    label=option[:80],
                    content=option[:200],
                )
                for index, option in enumerate(options[:5])
                if isinstance(option, str) and option.strip()
            ]
            if replies:
                return replies
        if draft.status == ChatbotTicketDraftStatus.COLLECTING:
            if draft.fields.category_id is None and draft.category_options:
                replies = [
                    ChatbotQuickReply(
                        id=f"draft-category-{index + 1}",
                        label=option.name,
                        content=f"Categoría: {option.name}",
                    )
                    for index, option in enumerate(draft.category_options[:5])
                ]
            elif (
                draft.missing_fields == [ChatbotTicketDraftField.PRIORITY]
                and not draft.validation_errors
            ):
                replies = [
                    ChatbotQuickReply(
                        id=f"draft-priority-{priority.value.casefold()}",
                        label=priority.value.title(),
                        content=f"Prioridad: {priority.value}",
                    )
                    for priority in TicketPriority
                ]
            else:
                replies = []
            replies.append(
                ChatbotQuickReply(
                    id="draft-cancel",
                    label="Cancelar borrador",
                    content="Cancelo el borrador",
                )
            )
            return replies

        intent = result.get("intent_classification") or result.get("intent")
        if (
            intent == ChatbotIntent.OUT_OF_SCOPE
            or result.get("fallback_reason") == "BANKING_OPERATION_OUT_OF_SCOPE"
        ):
            return [
                ChatbotQuickReply(
                    id="faq",
                    label="Preguntas frecuentes",
                    content="Preguntas frecuentes",
                ),
                ChatbotQuickReply(
                    id="create-ticket",
                    label="Crear solicitud",
                    content="Quiero crear un ticket",
                ),
            ]
        return [
            ChatbotQuickReply(
                id="my-tickets", label="Ver mis tickets", content="Mis tickets"
            ),
            ChatbotQuickReply(
                id="create-ticket",
                label="Crear solicitud",
                content="Quiero crear un ticket",
            ),
            ChatbotQuickReply(
                id="main-menu", label="Volver al menú", content="Menú principal"
            ),
            ChatbotQuickReply(
                id="contact-advisor",
                label="Hablar con un asesor",
                content="Quiero hablar con un asesor",
            ),
        ]

    @staticmethod
    def _message_ai_intent(message: ChatMessage) -> ChatbotIntent | None:
        trace = message.ai_trace_json
        if not isinstance(trace, dict):
            return None
        try:
            return ChatbotIntent(trace.get("intent_classification"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[a-z0-9áéíóúüñ]+", normalize_text(value))
            if token
        ]

    @classmethod
    def _meaningful_tokens(cls, value: str) -> list[str]:
        return [
            token
            for token in cls._tokens(value)
            if not token.isdigit() and token not in _STOP_WORDS
        ]

    @staticmethod
    def _contains_arithmetic_expression(value: str) -> bool:
        return bool(_ARITHMETIC_EXPRESSION_PATTERN.search(value))

    @staticmethod
    def _is_obviously_out_of_scope(value: str) -> bool:
        return bool(_OBVIOUS_OUT_OF_SCOPE_PATTERN.search(normalize_text(value)))

    @classmethod
    def _token_forms(cls, value: str) -> set[str]:
        forms: set[str] = set()
        for token in cls._tokens(value):
            forms.add(token)
            if len(token) > 4 and token.endswith("es"):
                forms.add(token[:-2])
            elif len(token) > 4 and token.endswith("s"):
                forms.add(token[:-1])
        return forms

    @staticmethod
    def _fuzzy_overlap(query_terms: set[str], candidate_terms: set[str]) -> float:
        eligible = {term for term in query_terms if len(term) >= 5}
        if not eligible:
            return 0.0
        matched = sum(
            1
            for term in eligible
            if any(
                SequenceMatcher(None, term, candidate).ratio() >= 0.80
                for candidate in candidate_terms
                if len(candidate) >= 5
            )
        )
        return matched / len(query_terms)

    @classmethod
    def _keywords(cls, raw_keywords: str) -> list[str]:
        try:
            parsed = json.loads(raw_keywords)
            if isinstance(parsed, list):
                return [str(keyword) for keyword in parsed]
        except json.JSONDecodeError:
            pass
        return [
            keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()
        ]

    @classmethod
    def _json_terms(cls, raw_terms: str) -> list[str]:
        try:
            parsed = json.loads(raw_terms)
        except json.JSONDecodeError:
            return []
        return [str(term) for term in parsed] if isinstance(parsed, list) else []

    def _category_name(self, session: Session, category_id: str) -> str | None:
        return self.conversations.category_name(session, category_id)

    def _intent_for(self, faq: FAQ) -> str | None:
        if faq.intent:
            return faq.intent
        question = self.normalize_text(faq.question)
        if "tarjeta" in question and "requisito" in question:
            return "TARJETAS_REQUISITOS"
        if "banca digital" in question:
            return "BANCA_DIGITAL_ACCESO"
        return None
