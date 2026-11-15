from app.modules.chatbot.services.chatbot_service import ChatbotService


def test_deterministic_intents_take_priority_over_generic_question_handling() -> None:
    cases = {
        "¿Cuál es mi saldo?": "OUT_OF_SCOPE",
        "¿Cómo va mi ticket TCK-20261115-AB12CD34EF?": "TICKET_QUERY",
        "¿Cuál es el estado de mi solicitud?": "TICKET_QUERY",
        (
            "¿Qué canal público menciona la solicitud de un duplicado de "
            "estado de cuenta?"
        ): "FAQ_QUERY",
        "Quiero crear un ticket": "CREATE_TICKET",
        "Quiero hablar con un asesor": "REQUEST_ADVISOR",
        "feedback": "FEEDBACK",
        "hola": "GREETING",
        "¿Qué requisitos tiene una tarjeta?": "FAQ_QUERY",
        "¿Qué puedes hacer por mí?": "FAQ_DISCOVERY",
        "¿Qué clima habrá mañana?": "OUT_OF_SCOPE",
    }

    for content, expected in cases.items():
        assert ChatbotService._classify_deterministic_intent(content).value == expected


def test_detects_only_banking_operations_named_in_the_scope_rule() -> None:
    assert ChatbotService._is_banking_operation_request("¿Me dices mi saldo?")
    assert ChatbotService._is_banking_operation_request(
        "Quiero hacer una transferencia"
    )
    assert ChatbotService._is_banking_operation_request(
        "¿Puedes consultar mis movimientos?"
    )
    assert not ChatbotService._is_banking_operation_request(
        "Descripción: No aparece el movimiento en la cuenta."
    )
    assert not ChatbotService._is_banking_operation_request(
        "Quiero crear un ticket por una transferencia no recibida."
    )
    assert not ChatbotService._is_banking_operation_request(
        "¿Qué requisitos tiene una tarjeta?"
    )


def test_banking_operation_response_offers_only_supported_ticket_help() -> None:
    result = ChatbotService._banking_operation_response()

    assert result["intent"] == "OUT_OF_SCOPE"
    assert "no realiza operaciones bancarias" in result["answer"]
    assert "preguntas frecuentes" in result["answer"]
    assert "ticket de soporte" in result["answer"]
    assert result["response_source"] == "DETERMINISTIC"
    assert result["available_actions"]
