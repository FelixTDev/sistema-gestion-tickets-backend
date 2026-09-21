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
from app.modules.chatbot.models.conversation import Conversation, ConversationStatus
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.chatbot.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.chatbot.schemas.chatbot import (
    BotMessageResponse,
    ChatFeedbackCreate,
    ChatFeedbackRead,
    ChatMessageRead,
    ConversationRead,
)
from app.modules.chatbot.services.guardrails import detect_prompt_injection
from app.modules.chatbot.services.rag_service import RAGService
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.notificaciones.models.notification import NotificationType
from app.modules.notificaciones.services.notification_service import NotificationService
from app.shared.text import normalize_text

_STOP_WORDS = {
    "a",
    "al",
    "como",
    "con",
    "de",
    "del",
    "el",
    "en",
    "la",
    "las",
    "los",
    "me",
    "necesito",
    "para",
    "por",
    "que",
    "quiero",
    "y",
}
_MARKUP_PATTERN = re.compile(r"<[^>]+>|javascript\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class FAQMatch:
    faq: FAQ
    score: float
    intent: str | None
    category_name: str | None


class ChatbotService:
    def __init__(
        self,
        conversation_repository: ConversationRepository | None = None,
        faq_repository: FAQRepository | None = None,
        notification_service: NotificationService | None = None,
        audit_service: AuditService | None = None,
        ai_provider: object | None = None,
        rag_service: RAGService | None = None,
    ) -> None:
        self.conversations = conversation_repository or ConversationRepository()
        self.faqs = faq_repository or FAQRepository()
        self.notifications = notification_service or NotificationService()
        self.audit = audit_service or AuditService()
        self.rag = rag_service or RAGService(provider=ai_provider)

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

        user_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.USER,
                content=clean_content,
            ),
        )
        matches = self._find_matches(session, clean_content)
        result = self._response_for_matches(conversation, matches)
        result["response_source"] = result.get("response_source", "DETERMINISTIC")
        result["sources"] = self._public_sources(matches[:1])
        requests_last_minute = self.conversations.count_recent_ai_messages(
            session,
            conversation.id,
            datetime.now(UTC) - timedelta(minutes=1),
        )
        ai_outcome = self.rag.generate(
            conversation_id=conversation.id,
            user_message=clean_content,
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
        conversation.category_id = (
            result["match"].faq.category_id if result["match"] else None
        )
        conversation.detected_intent = result["intent"]
        conversation.pending_question = (
            "Selecciona la opción que describe mejor tu consulta."
            if result["requires_clarification"]
            else None
        )
        context = dict(conversation.context_json or {})
        if result["requires_clarification"]:
            context["clarification_attempts"] = (
                int(context.get("clarification_attempts", 0)) + 1
            )
        else:
            context["clarification_attempts"] = 0
        context["last_terms"] = self._meaningful_tokens(clean_content)[:20]
        conversation.context_json = context
        if result["escalated"]:
            conversation.status = ConversationStatus.ESCALATED
            conversation.escalation_reason = result["fallback_reason"]
            conversation.escalated_at = conversation.last_activity_at
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
            fallback_reason=result["fallback_reason"],
            conversation_status=conversation.status.value,
            response_source=result["response_source"],
            sources=result["sources"],
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
        if conversation.last_faq_id is None:
            raise HTTPException(
                status_code=422, detail="La conversación no tiene una FAQ evaluable"
            )
        feedback = FAQFeedback(
            faq_id=conversation.last_faq_id,
            user_id=current_user.user.id if current_user else None,
            is_helpful=data.is_helpful,
            escalation_accepted=data.escalation_accepted,
            comment=data.reason,
        )
        session.add(feedback)
        session.flush()
        self.audit.record(
            session,
            event_type="CHATBOT",
            action="FEEDBACK_RECEIVED",
            actor_user_id=current_user.user.id if current_user else None,
            actor_role=current_user.role if current_user else None,
            resource_type="CONVERSATION",
            resource_id=conversation.id,
            target_user_id=conversation.user_id,
            success=True,
            metadata={
                "faq_id": conversation.last_faq_id,
                "is_helpful": data.is_helpful,
                "escalation_accepted": data.escalation_accepted,
            },
        )
        session.commit()
        session.refresh(feedback)
        return ChatFeedbackRead(
            id=feedback.id,
            is_helpful=feedback.is_helpful,
            escalation_accepted=feedback.escalation_accepted,
            created_at=feedback.created_at,
        )

    def _find_matches(self, session: Session, content: str) -> list[FAQMatch]:
        query = self.normalize_text(content)
        query_terms = self._meaningful_tokens(query)
        if not query_terms:
            return []
        matches: list[FAQMatch] = []
        for faq in self.faqs.list_active(session):
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
                "offers_ticket": True,
                "requires_clarification": True,
                "options": options,
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
                "fallback_reason": None,
                "escalated": False,
                "match": best,
                "response_source": "DETERMINISTIC",
            }
        return {
            "answer": (
                "No encontré una respuesta confiable para esta consulta. "
                "Puedes solicitar atención humana o convertir la conversación "
                "en un ticket."
            ),
            "intent": best.intent if best else None,
            "faq_id": best.faq.id if best else None,
            "category_name": best.category_name if best else None,
            "confidence": confidence,
            "resolved": False,
            "offers_ticket": True,
            "requires_clarification": False,
            "options": [],
            "fallback_reason": "LOW_CONFIDENCE",
            "escalated": True,
            "match": best,
            "response_source": "FALLBACK",
        }

    @staticmethod
    def _public_sources(
        matches: list[FAQMatch] | tuple[object, ...],
    ) -> list[dict[str, str | None]]:
        sources: list[dict[str, str | None]] = []
        for item in matches:
            if isinstance(item, FAQMatch):
                sources.append(
                    {
                        "title": item.faq.title or item.faq.question,
                        "category": item.category_name,
                    }
                )
            else:
                sources.append({"title": item.title, "category": item.category})
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
