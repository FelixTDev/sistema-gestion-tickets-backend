from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage, SenderType
from app.modules.conocimiento.models.faq import FAQ


class ConversationRepository:
    def add_conversation(
        self, session: Session, conversation: Conversation
    ) -> Conversation:
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation

    def get_conversation(
        self, session: Session, conversation_id: str
    ) -> Conversation | None:
        return session.get(Conversation, conversation_id)

    def list_messages(
        self, session: Session, conversation_id: str
    ) -> list[ChatMessage]:
        return list(
            session.exec(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at)
            ).all()
        )

    def add_message(self, session: Session, message: ChatMessage) -> ChatMessage:
        session.add(message)
        session.commit()
        session.refresh(message)
        return message

    def count_recent_messages(
        self, session: Session, conversation_id: str, since: datetime
    ) -> int:
        return int(
            session.exec(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.sender_type == SenderType.USER,
                    ChatMessage.created_at >= since,
                )
            ).one()
        )

    def count_recent_anonymous_conversations(
        self, session: Session, anonymous_key: str, since: datetime
    ) -> int:
        return int(
            session.exec(
                select(func.count(Conversation.id)).where(
                    Conversation.anonymous_key == anonymous_key,
                    Conversation.started_at >= since,
                )
            ).one()
        )

    def count_recent_ai_messages(
        self, session: Session, conversation_id: str, since: datetime
    ) -> int:
        return int(
            session.exec(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.sender_type == SenderType.BOT,
                    ChatMessage.response_source == "AI",
                    ChatMessage.created_at >= since,
                )
            ).one()
        )

    def count_ai_messages(self, session: Session, conversation_id: str) -> int:
        return int(
            session.exec(
                select(func.count(ChatMessage.id)).where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.sender_type == SenderType.BOT,
                    ChatMessage.response_source == "AI",
                )
            ).one()
        )

    def category_name(self, session: Session, category_id: str | None) -> str | None:
        if category_id is None:
            return None
        from app.modules.conocimiento.models.category import TicketCategory

        category = session.get(TicketCategory, category_id)
        return category.name if category is not None else None

    def active_faqs(self, session: Session) -> list[FAQ]:
        return list(session.exec(select(FAQ).where(FAQ.is_active)).all())
