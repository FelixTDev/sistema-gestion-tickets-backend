from sqlalchemy import case, func
from sqlmodel import Session, select

from app.modules.chatbot.models.feedback import ChatbotFeedback


class ChatbotFeedbackRepository:
    def get_by_message(
        self, session: Session, message_id: str
    ) -> ChatbotFeedback | None:
        return session.exec(
            select(ChatbotFeedback).where(ChatbotFeedback.message_id == message_id)
        ).first()

    def add(self, session: Session, feedback: ChatbotFeedback) -> ChatbotFeedback:
        session.add(feedback)
        session.flush()
        return feedback

    def metrics(self, session: Session) -> tuple[int, int, int]:
        helpful = func.coalesce(
            func.sum(case((ChatbotFeedback.is_helpful.is_(True), 1), else_=0)), 0
        )
        faq_backed = func.coalesce(
            func.sum(case((ChatbotFeedback.faq_id.is_not(None), 1), else_=0)), 0
        )
        total, helpful_count, faq_count = session.exec(
            select(func.count(ChatbotFeedback.id), helpful, faq_backed)
        ).one()
        return int(total or 0), int(helpful_count or 0), int(faq_count or 0)
