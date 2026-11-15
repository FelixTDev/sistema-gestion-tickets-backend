"""Short, neutral Spanish copy for deterministic chatbot lifecycle messages."""

PERSONA_TEXTS = {
    "greeting": (
        "¡Hola! Soy el asistente virtual del prototipo académico de gestión de "
        "tickets de Banco GNB Perú. No es un canal bancario oficial ni procesa "
        "operaciones bancarias. ¿En qué "
        "puedo ayudarte hoy? Puedo orientarte sobre preguntas frecuentes o el "
        "seguimiento de una solicitud."
    ),
    "error": (
        "No puedo consultar la información de ayuda en este momento. "
        "Inténtalo de nuevo más tarde."
    ),
    "menu_return": (
        "Volví al menú. Elige un tema o escribe otra consulta; "
        "el historial de esta conversación se conserva."
    ),
    "farewell": (
        "La conversación finalizó y su historial se conserva. "
        "No se creó ningún ticket."
    ),
}


def persona_text(key: str, *, enabled: bool, fallback: str) -> str:
    if not enabled:
        return fallback
    return PERSONA_TEXTS.get(key, fallback)
