import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.auditoria.services.audit_service import AuditService
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
    ChatbotFeedbackMetrics,
    ChatbotSuggestedAction,
    ChatbotTicketCard,
    ChatbotTicketDraftStatus,
    ChatbotTicketPagination,
    ChatbotTicketQuery,
    ChatbotTicketQueryType,
    ChatFeedbackCreate,
    ChatFeedbackRead,
    ChatMessageRead,
    ConversationRead,
)
from app.modules.chatbot.services.guardrails import (
    detect_prompt_injection,
    has_verified_official_source,
)
from app.modules.chatbot.services.rag_service import RAGService
from app.modules.chatbot.services.ticket_draft_service import (
    TicketDraftReply,
    TicketDraftService,
)
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.modules.tickets.models.ticket import TicketPriority, TicketStatus
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
    "el",
    "en",
    "la",
    "las",
    "los",
    "me",
    "necesito",
    "no",
    "para",
    "por",
    "que",
    "quiero",
    "y",
}
_MARKUP_PATTERN = re.compile(r"<[^>]+>|javascript\s*:", re.IGNORECASE)
_TICKET_REQUEST_PATTERN = re.compile(
    r"\b(?:quiero|necesito|mejor|genera|generar|crea|crear|abre|abrir|"
    r"registra|registrar|levanta|solicito|solicitar|haz|hacer)\b"
    r".{0,40}\b(?:ticket|caso|solicitud|reclamo)\b"
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
)
_UNHELPFUL_FOLLOW_UP_PATTERN = re.compile(
    r"\b(?:no\s+me\s+(?:ayudo|sirvio|resolvio|respondio)|"
    r"no\s+fue\s+(?:util|suficiente)|no\s+me\s+ayuda)\b"
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
_GREETING_ANSWER = (
    "¡Hola! ¿En qué puedo ayudarte hoy? Puedo orientarte sobre preguntas "
    "frecuentes o el seguimiento de una solicitud."
)
_FAQ_DISCOVERY_PHRASES = (
    "preguntas frecuentes",
    "que preguntas me puedes dar",
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
_FAQ_DISCOVERY_MAX_OPTIONS = 5
_FAQ_DISCOVERY_CATEGORY_ORDER = (
    "TARJETAS",
    "CUENTAS",
    "BANCA_DIGITAL",
    "CREDITOS",
    "RECLAMOS_SIMPLES",
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
        return self._conversation_read(session, conversation)

    def send_message(
        self,
        session: Session,
        conversation_id: str,
        content: str,
        current_user: AuthenticatedUser | None,
    ) -> BotMessageResponse:
        self._require_public_actor(current_user)
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        self._ensure_message_allowed(conversation)
        clean_content = self._clean_content(content)
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
        is_ticket_request = self._is_ticket_request(clean_content)
        is_advisor_request = self._is_advisor_request(clean_content)
        ticket_query_request = self._ticket_query_request(clean_content)
        ticket_query_request = self._resume_clarified_ticket_query(
            clean_content, conversation, ticket_query_request
        )
        is_unhelpful_follow_up = bool(
            previous_bot_message and self._is_unhelpful_follow_up(clean_content)
        )
        is_greeting = self._is_pure_greeting(clean_content)
        is_faq_discovery = self._is_faq_discovery_request(clean_content)
        saved_draft = (conversation.context_json or {}).get(TICKET_DRAFT_CONTEXT_KEY)
        current_draft = self.ticket_drafts.read(session, saved_draft)
        resume_after_authentication = bool(
            current_user is not None
            and previous_bot_message is not None
            and previous_bot_message.intent == "TICKET_CREATION_AUTHENTICATION_REQUIRED"
            and self.ticket_drafts.is_resume_request(clean_content)
        )
        start_ticket_draft = is_ticket_request or resume_after_authentication
        unrelated_special_message = not start_ticket_draft and (
            is_greeting or is_faq_discovery or is_unhelpful_follow_up
        )
        ticket_draft_flow = (
            not unrelated_special_message
            and self.ticket_drafts.should_handle(
                session,
                clean_content,
                current_draft,
                start_requested=start_ticket_draft,
            )
        )

        user_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.USER,
                content=clean_content,
            ),
        )
        search_content = clean_content
        ai_user_message = clean_content
        selected_match: FAQMatch | None = None
        matches: list[FAQMatch] = []
        retrieval_unavailable = False
        ticket_draft_reply: TicketDraftReply | None = None
        try:
            if ticket_query_request is not None:
                result = self._ticket_query_response(
                    session, current_user, ticket_query_request
                )
            elif ticket_draft_flow and current_user is None:
                result = self._ticket_authentication_response(conversation)
            elif is_advisor_request and is_ticket_request:
                result = self._action_clarification_response(conversation)
            elif is_advisor_request:
                result = self._advisor_request_response(conversation)
            elif ticket_draft_flow:
                if conversation.user_id is None:
                    self.link_user(session, conversation.id, current_user)
                ticket_draft_reply = self.ticket_drafts.handle(
                    session,
                    clean_content,
                    saved_draft,
                    start_requested=start_ticket_draft,
                )
                result = self._ticket_draft_response(conversation, ticket_draft_reply)
            elif is_unhelpful_follow_up:
                result = self._unhelpful_follow_up_response(conversation)
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
                    matches = self._find_matches(session, clean_content)
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

        result["response_source"] = result.get("response_source", "DETERMINISTIC")
        result["sources"] = result.get("sources", self._public_sources(matches[:1]))
        skip_ai = bool(
            retrieval_unavailable
            or ticket_query_request is not None
            or is_ticket_request
            or is_advisor_request
            or is_unhelpful_follow_up
            or (is_advisor_request and is_ticket_request)
            or is_greeting
            or is_faq_discovery
            or selected_match is not None
            or current_draft.status
            in {
                ChatbotTicketDraftStatus.COLLECTING,
                ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
            }
            or ticket_draft_reply is not None
        )
        if skip_ai:
            result["ai_trace"] = (
                {"fallback": True, "fallback_reason": "KNOWLEDGE_UNAVAILABLE"}
                if retrieval_unavailable
                else None
            )
        else:
            requests_last_minute = self.conversations.count_recent_ai_messages(
                session,
                conversation.id,
                datetime.now(UTC) - timedelta(minutes=1),
            )
            ai_outcome = self.rag.generate(
                conversation_id=conversation.id,
                user_message=ai_user_message,
                matches=matches,
                requests_last_minute=requests_last_minute,
                responses_in_conversation=self.conversations.count_ai_messages(
                    session, conversation.id
                ),
            )
            result["ai_trace"] = ai_outcome.trace
            if ai_outcome.accepted:
                result["answer"] = ai_outcome.answer
                result["resolved"] = True
                result["offers_ticket"] = False
                result["requires_clarification"] = False
                result["options"] = []
                result["candidate_faq_ids"] = []
                result["response_source"] = "AI"
                result["sources"] = self._public_sources(ai_outcome.sources)
            action = (
                "AI_ACCEPTED"
                if ai_outcome.accepted
                else (
                    "AI_GUARDRAIL_REJECTED"
                    if ai_outcome.reason
                    in {
                        "USER_INJECTION",
                        "SENSITIVE_REQUEST",
                        "CONTEXT_INJECTION",
                        "UNSAFE_OUTPUT",
                        "INVALID_SOURCES",
                        "OUT_OF_CONTEXT",
                        "OUTPUT_SIZE",
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
                success=ai_outcome.accepted or action == "AI_FALLBACK",
                error_code=None if ai_outcome.accepted else ai_outcome.reason,
                metadata={
                    "provider": ai_outcome.trace.get("provider"),
                    "model": ai_outcome.trace.get("model"),
                    "source_count": len(ai_outcome.sources),
                    "fallback": not ai_outcome.accepted,
                },
                dedupe_key=f"ai:{conversation.id}:{user_message.id}",
            )

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
        if ticket_draft_reply is not None:
            if ticket_draft_reply.draft.status in {
                ChatbotTicketDraftStatus.COLLECTING,
                ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
                ChatbotTicketDraftStatus.CANCELLED,
            }:
                context[TICKET_DRAFT_CONTEXT_KEY] = self.ticket_drafts.persisted(
                    ticket_draft_reply.draft
                )
            else:
                context.pop(TICKET_DRAFT_CONTEXT_KEY, None)
        pending_ticket_query = result.get("pending_ticket_query")
        if isinstance(pending_ticket_query, dict):
            context["pending_ticket_query"] = pending_ticket_query
        else:
            context.pop("pending_ticket_query", None)
        if ticket_draft_reply is not None:
            context.pop("clarification_attempts", None)
            context.pop("pending_faq_ids", None)
            context.pop("clarification_context", None)
        else:
            if result["requires_clarification"] and result.get(
                "counts_as_clarification", True
            ):
                context["clarification_attempts"] = (
                    int(context.get("clarification_attempts", 0)) + 1
                )
            elif result["resolved"]:
                context["clarification_attempts"] = 0
            if result["requires_clarification"]:
                context["pending_faq_ids"] = result.get("candidate_faq_ids", [])
                if result.get("intent") == "FAQ_DISCOVERY":
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
                context.pop("pending_faq_ids", None)
                context.pop("clarification_context", None)
        if not result.get("preserve_context"):
            context["last_terms"] = self._meaningful_tokens(search_content)[:20]
        conversation.context_json = context
        if result["escalated"]:
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
        return BotMessageResponse(
            user_message=self._message_read(user_message),
            bot_message=self._message_read(bot_message),
            resolved=result["resolved"],
            offers_ticket=result["offers_ticket"],
            confidence=result["confidence"],
            faq_id=result["faq_id"],
            source_category=result["category_name"],
            requires_clarification=result["requires_clarification"],
            clarification_options=result["options"],
            suggested_action=result.get(
                "suggested_action", self._suggested_action(result)
            ),
            fallback_reason=result["fallback_reason"],
            conversation_status=conversation.status,
            response_source=result["response_source"],
            sources=result["sources"],
            ticket_query=result.get("ticket_query"),
            ticket_draft=self.ticket_drafts.read(
                session, conversation.context_json.get(TICKET_DRAFT_CONTEXT_KEY)
            ),
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
            return self._conversation_read(session, conversation)
        now = datetime.now(UTC)
        conversation.status = ConversationStatus.ESCALATED
        conversation.escalation_reason = reason or "USER_REQUESTED"
        conversation.escalated_at = now
        conversation.last_activity_at = now
        session.add(conversation)
        if conversation.user_id is not None:
            self.notifications.create(
                session,
                recipient_user_id=conversation.user_id,
                notification_type=NotificationType.CHAT_ESCALATED,
                title="Conversación derivada",
                message="Tu consulta fue derivada para atención adicional.",
                related_conversation_id=conversation.id,
                metadata={"reason": conversation.escalation_reason},
                dedupe_key=f"chat_escalated:manual:{conversation.id}",
            )
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
        session.commit()
        session.refresh(conversation)
        return self._conversation_read(session, conversation)

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
        return self._conversation_read(session, conversation)

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

    def _find_matches(self, session: Session, content: str) -> list[FAQMatch]:
        query = self.normalize_text(content)
        query_terms = self._meaningful_tokens(query)
        if not query_terms:
            return []
        matches: list[FAQMatch] = []
        for faq in self.faqs.list_active(session):
            if not has_verified_official_source(faq.source_url, faq.source_verified_at):
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
            query_forms = self._token_forms(" ".join(query_terms))
            overlap = len(query_forms & field_tokens) / len(query_forms)
            keyword_overlap = len(query_forms & keyword_tokens) / len(query_forms)
            score = min(1.0, 0.55 * overlap + 0.45 * keyword_overlap)
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

    def _faq_discovery_response(
        self, session: Session
    ) -> tuple[dict[str, object], list[FAQMatch]]:
        available_faqs = [
            faq
            for faq in self.faqs.list_active(session)
            if has_verified_official_source(faq.source_url, faq.source_verified_at)
        ]
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
                    and not detect_prompt_injection(
                        f"{candidate.title} {candidate.question} "
                        f"{candidate.summary} {candidate.answer}"
                    )
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
                if faq.category_id in seen_categories or detect_prompt_injection(
                    f"{faq.title} {faq.question} {faq.summary} {faq.answer}"
                ):
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
                if faq.id in selected_ids or detect_prompt_injection(
                    f"{faq.title} {faq.question} {faq.summary} {faq.answer}"
                ):
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
            and has_verified_official_source(faq.source_url, faq.source_verified_at)
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
    def _is_advisor_request(cls, content: str) -> bool:
        normalized = cls.normalize_text(content)
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
            re.search(r"\b(?:tickets?|casos?|solicitudes?|reclamos?)\b", normalized)
        )
        page_match = re.search(r"\bpagina\s+(\d+)\b", normalized)
        page = min(int(page_match.group(1)), 10_000) if page_match else 1

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

        asks_what_answered = bool(
            re.search(
                r"\b(?:que|cual).{0,35}\b(?:respondio|contesto)\b|"
                r"\b(?:respuesta|comentario)\b.{0,25}\b(?:asesor|ticket)\b",
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

        asks_status = (has_ticket_subject or has_code_marker) and bool(
            re.search(
                r"\b(?:estado|situacion|seguimiento)\b|\bcomo\s+(?:va|esta|sigue)\b",
                normalized,
            )
        )
        if asks_status:
            if has_code_marker and (len(codes) != 1 or code is None):
                return cls._clarify_ticket_code_request(
                    page,
                    action=ChatbotSuggestedAction.TICKET_STATUS,
                    query_type=ChatbotTicketQueryType.TICKET_STATUS,
                )
            if code is None:
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
            )

        asks_count = bool(
            re.search(
                r"\b(?:cuantos?|cuantas?)\s+(?:tickets?|casos?|solicitudes?)\b|"
                r"\b(?:tickets?|casos?|solicitudes?)\s+tengo\b",
                normalized,
            )
        )
        if asks_count:
            statuses = (
                _OPEN_TICKET_STATUSES
                if re.search(r"\b(?:abiertos?|activas?)\b", normalized)
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
            page_size = (
                1
                if request.query_type == ChatbotTicketQueryType.TICKET_COUNT
                else _MAX_CHAT_TICKETS
            )
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
            return self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )

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
            return self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )

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
            answer = (
                f"El ticket {ticket.tracking_code} está en estado {card.status_label}."
            )
            return self._ticket_query_result(
                answer, request.action, payload, resolved=True
            )

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
        )

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
    def _action_clarification_response(
        conversation: Conversation,
    ) -> dict[str, object]:
        return {
            "answer": (
                "¿Prefieres que solicite atención de un asesor o que preparemos "
                "un ticket?"
            ),
            "intent": "ACTION_CLARIFICATION",
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
        }

    @staticmethod
    def _advisor_request_response(
        conversation: Conversation,
    ) -> dict[str, object]:
        return {
            "answer": (
                "Entiendo. Solicitaré atención de un asesor para esta conversación. "
                "No compartas contraseñas, PIN, CVV, OTP ni códigos de seguridad."
            ),
            "intent": "ADVISOR_REQUEST",
            "faq_id": conversation.last_faq_id,
            "category_name": None,
            "confidence": conversation.last_confidence or 0.0,
            "resolved": False,
            "offers_ticket": False,
            "requires_clarification": False,
            "options": [],
            "candidate_faq_ids": [],
            "fallback_reason": "USER_REQUESTED",
            "escalated": True,
            "match": None,
            "response_source": "DETERMINISTIC",
            "preserve_context": True,
            "suggested_action": ChatbotSuggestedAction.CONTACT_ADVISOR,
        }

    @staticmethod
    def _ticket_authentication_response(
        conversation: Conversation,
    ) -> dict[str, object]:
        return {
            "answer": (
                "Para preparar y confirmar un ticket, inicia sesión o regístrate. "
                "Después podrás vincular y retomar esta conversación. No compartas "
                "contraseñas, OTP, CVV, tokens ni códigos de seguridad."
            ),
            "intent": "TICKET_CREATION_AUTHENTICATION_REQUIRED",
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
        conversation: Conversation, reply: TicketDraftReply
    ) -> dict[str, object]:
        if reply.cancelled:
            intent = "TICKET_DRAFT_CANCELLED"
            action = ChatbotSuggestedAction.NONE
        elif reply.ready:
            intent = "TICKET_DRAFT_READY"
            action = ChatbotSuggestedAction.CONVERT_TO_TICKET
        else:
            intent = "TICKET_DRAFT"
            action = ChatbotSuggestedAction.ASK_CLARIFICATION
        return {
            "answer": reply.answer,
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
            "ticket_query": None,
            "counts_as_clarification": reply.requires_clarification,
        }

    @staticmethod
    def _unhelpful_follow_up_response(
        conversation: Conversation,
    ) -> dict[str, object]:
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
            "answer": (
                "No puedo consultar la información de ayuda en este momento. "
                "Inténtalo de nuevo más tarde."
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
        return (
            "Usa este diálogo reciente solo para entender la referencia de la "
            "consulta actual. Basa cualquier dato del producto únicamente en las "
            "fuentes autorizadas que acompañan la solicitud.\n"
            f"Mensaje previo del cliente: {previous_user_message[:250]}\n"
            f"Respuesta previa del asistente: {previous_bot_message[:1000]}\n"
            f"Consulta actual: {current_message}"
        )

    @staticmethod
    def _suggested_action(result: dict[str, object]) -> ChatbotSuggestedAction:
        if result.get("escalated"):
            return ChatbotSuggestedAction.CONTACT_ADVISOR
        if result.get("requires_clarification"):
            return ChatbotSuggestedAction.ASK_CLARIFICATION
        if result.get("offers_ticket"):
            return ChatbotSuggestedAction.CONVERT_TO_TICKET
        return ChatbotSuggestedAction.NONE

    @staticmethod
    def _greeting_response() -> dict[str, object]:
        return {
            "answer": _GREETING_ANSWER,
            "intent": None,
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
        }

    @staticmethod
    def _public_sources(
        matches: list[FAQMatch] | tuple[object, ...],
    ) -> list[dict[str, object]]:
        sources: list[dict[str, object]] = []
        for item in matches:
            if isinstance(item, FAQMatch):
                sources.append(
                    {
                        "title": item.faq.title or item.faq.question,
                        "category": item.category_name,
                        "url": item.faq.source_url,
                        "verified_at": item.faq.source_verified_at,
                    }
                )
            else:
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
        self, session: Session, conversation: Conversation
    ) -> ConversationRead:
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
            ticket_draft=self.ticket_drafts.read(
                session,
                (conversation.context_json or {}).get(TICKET_DRAFT_CONTEXT_KEY),
            ),
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
            response_source=message.response_source,
            created_at=message.created_at,
        )

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [
            token
            for token in re.findall(r"[a-z0-9áéíóúüñ]+", normalize_text(value))
            if token
        ]

    @classmethod
    def _meaningful_tokens(cls, value: str) -> list[str]:
        return [token for token in cls._tokens(value) if token not in _STOP_WORDS]

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
