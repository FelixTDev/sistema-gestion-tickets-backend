from app.modules.chatbot.models.conversation import Conversation
from app.modules.chatbot.models.message import ChatMessage
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.tickets.models.assignment import TicketAssignment
from app.modules.tickets.models.comment import TicketComment
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket
from app.modules.usuarios.models.role import Role
from app.modules.usuarios.models.user import User

__all__ = [
    "ChatMessage",
    "Conversation",
    "FAQ",
    "Role",
    "Ticket",
    "TicketAssignment",
    "TicketCategory",
    "TicketComment",
    "TicketHistory",
    "User",
]
