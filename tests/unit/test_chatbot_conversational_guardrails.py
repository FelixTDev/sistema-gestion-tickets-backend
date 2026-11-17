from app.modules.chatbot.services.guardrails import validate_conversational_output


def test_general_issuer_safety_guidance_is_allowed_without_a_faq_source() -> None:
    decision = validate_conversational_output(
        "Contacta al emisor por un canal oficial. Este sistema no puede bloquear "
        "tarjetas. ¿Quieres que prepare una solicitud para un asesor?",
        (),
        1200,
        intent_classification="CLARIFICATION",
    )

    assert decision.allowed is True


def test_issuer_guidance_allowlist_does_not_allow_specific_claims() -> None:
    decision = validate_conversational_output(
        "Contacta al emisor por un canal oficial. Te atenderán de inmediato.",
        (),
        1200,
        intent_classification="CLARIFICATION",
    )

    assert decision.allowed is False
    assert decision.reason == "UNGROUNDED_CLAIM"


def test_conversational_output_cannot_claim_a_card_was_blocked() -> None:
    for answer in (
        "Listo, ya bloqueé tu tarjeta.",
        "Tu tarjeta está bloqueada.",
        "Bloquea tu tarjeta ya.",
        "Te recomiendo bloquear tu tarjeta.",
    ):
        decision = validate_conversational_output(
            answer,
            (),
            1200,
            intent_classification="CLARIFICATION",
        )
        assert decision.allowed is False


def test_advisor_response_cannot_claim_a_live_transfer_or_created_ticket() -> None:
    for answer in (
        "Te transfiero con un asesor humano en vivo.",
        "Ya he creado un ticket para que te atiendan.",
    ):
        decision = validate_conversational_output(
            answer,
            (),
            1200,
            intent_classification="REQUEST_ADVISOR",
        )
        assert decision.allowed is False
