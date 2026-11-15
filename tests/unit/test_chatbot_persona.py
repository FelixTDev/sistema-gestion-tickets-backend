from app.modules.chatbot.config.persona import PERSONA_TEXTS, persona_text


def test_persona_greeting_disclaims_official_channel_and_bank_operations() -> None:
    greeting = persona_text("greeting", enabled=True, fallback="fallback")

    assert "prototipo académico" in greeting
    normalized = greeting.casefold()
    assert "no es un canal bancario oficial" in normalized
    assert "ni procesa operaciones bancarias" in normalized
    assert "preguntas frecuentes" in normalized
    assert "seguimiento de una solicitud" in normalized


def test_persona_flag_off_uses_caller_fallback() -> None:
    assert persona_text("greeting", enabled=False, fallback="saludo previo") == (
        "saludo previo"
    )
    assert PERSONA_TEXTS["error"]
