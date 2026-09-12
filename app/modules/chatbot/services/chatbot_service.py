import json
import re
import unicodedata
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.chatbot.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.chatbot.schemas.chatbot import (
    BotMessageResponse,
    ChatMessageRead,
    ConversationRead,
    LinkConversationResponse,
)
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.repositories.faq_repository import FAQRepository


@dataclass(frozen=True)
class FAQMatch:
    faq: FAQ
    intent: str | None


class ChatbotService:
    def __init__(
        self,
        conversation_repository: ConversationRepository | None = None,
        faq_repository: FAQRepository | None = None,
    ) -> None:
        self.conversations = conversation_repository or ConversationRepository()
        self.faqs = faq_repository or FAQRepository()

    @staticmethod
    def normalize_text(value: str) -> str:
        without_accents = "".join(
            char
            for char in unicodedata.normalize("NFD", value)
            if unicodedata.category(char) != "Mn"
        )
        return re.sub(r"\s+", " ", without_accents).strip().casefold()

    def create_conversation(
        self, session: Session, current_user: AuthenticatedUser | None
    ) -> Conversation:
        user_id = current_user.user.id if current_user else None
        return self.conversations.add_conversation(
            session, Conversation(user_id=user_id)
        )

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
        conversation = self._authorized_conversation(
            session, conversation_id, current_user
        )
        user_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.USER,
                content=content.strip(),
            ),
        )
        match = self._find_match(session, content)
        if match is None:
            answer = (
                "No encontré una respuesta confiable para esta consulta. "
                "Puedes continuarla creando un ticket en la siguiente fase."
            )
            intent = None
            resolved = False
            offers_ticket = True
        else:
            answer = match.faq.answer
            intent = match.intent
            resolved = True
            offers_ticket = False
        bot_message = self.conversations.add_message(
            session,
            ChatMessage(
                conversation_id=conversation.id,
                sender_type=SenderType.BOT,
                content=answer,
                intent=intent,
                confidence=1.0 if resolved else 0.0,
            ),
        )
        return BotMessageResponse(
            user_message=self._message_read(user_message),
            bot_message=self._message_read(bot_message),
            resolved=resolved,
            offers_ticket=offers_ticket,
        )

    def link_user(
        self,
        session: Session,
        conversation_id: str,
        current_user: AuthenticatedUser,
    ) -> LinkConversationResponse:
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
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return LinkConversationResponse(
            id=conversation.id, user_id=conversation.user_id, status=conversation.status
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
        if conversation.user_id is None:
            return conversation
        if current_user is None:
            raise HTTPException(status_code=401, detail="Autenticación requerida")
        if conversation.user_id != current_user.user.id and current_user.role not in {
            "ASESOR",
            "SUPERVISOR",
        }:
            raise HTTPException(status_code=403, detail="Conversación no autorizada")
        return conversation

    def _find_match(self, session: Session, content: str) -> FAQMatch | None:
        normalized_content = self.normalize_text(content)
        best_match: FAQMatch | None = None
        best_score = 0
        for faq in self.faqs.list_active(session):
            keywords = self._keywords(faq.keywords)
            score = sum(
                1
                for keyword in keywords
                if self.normalize_text(keyword) in normalized_content
            )
            if score > best_score:
                best_score = score
                best_match = FAQMatch(faq=faq, intent=self._intent_for(faq))
        return best_match

    @staticmethod
    def _keywords(raw_keywords: str) -> list[str]:
        try:
            parsed = json.loads(raw_keywords)
            if isinstance(parsed, list):
                return [str(keyword) for keyword in parsed]
        except json.JSONDecodeError:
            pass
        return [
            keyword.strip() for keyword in raw_keywords.split(",") if keyword.strip()
        ]

    def _intent_for(self, faq: FAQ) -> str | None:
        question = self.normalize_text(faq.question)
        if "tarjeta" in question and "requisito" in question:
            return "TARJETAS_REQUISITOS"
        if "banca digital" in question:
            return "BANCA_DIGITAL_ACCESO"
        return None

    def _conversation_read(
        self, session: Session, conversation: Conversation
    ) -> ConversationRead:
        return ConversationRead(
            id=conversation.id,
            user_id=conversation.user_id,
            status=conversation.status,
            started_at=conversation.started_at,
            ended_at=conversation.ended_at,
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
            created_at=message.created_at,
        )
