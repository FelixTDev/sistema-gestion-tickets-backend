from sqlmodel import Session, select

from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage
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

    def active_faqs(self, session: Session) -> list[FAQ]:
        return list(session.exec(select(FAQ).where(FAQ.is_active)).all())
