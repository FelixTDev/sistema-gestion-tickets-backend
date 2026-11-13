from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
    NoopAIProvider,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.guardrails import (
    minimize_provider_input,
    validate_output,
)
from app.modules.chatbot.services.rag_service import RAGService, _RuntimeState


class StaticFAQRepository:
    def __init__(self, faq: SimpleNamespace) -> None:
        self.faq = faq

    def list_active(self, _session: object) -> list[SimpleNamespace]:
        return [self.faq]


class StaticConversationRepository:
    @staticmethod
    def category_name(_session: object, _category_id: str) -> str:
        return "TARJETAS"


class CountingProvider:
    name = "test-provider"
    model = "test-model"

    def __init__(self, result: AIGenerationResult | None = None) -> None:
        self.calls = 0
        self.result = result or AIGenerationResult.generated(
            text="Bloquea la tarjeta desde un canal oficial.",
            source_ids=["faq-card-block"],
            provider=self.name,
            model=self.model,
        )

    def generate(self, _request: object) -> AIGenerationResult:
        self.calls += 1
        return self.result


class CapturingProvider:
    name = "test-provider"
    model = "test-model"

    def __init__(self, result: AIGenerationResult) -> None:
        self.result = result
        self.requests: list[AIProviderRequest] = []

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        self.requests.append(request)
        return self.result


def faq(
    *,
    faq_id: str = "faq-card-block",
    question: str = "¿Cómo bloqueo una tarjeta perdida?",
    title: str = "Bloqueo de tarjeta",
    summary: str = "Bloquea la tarjeta perdida desde un canal oficial.",
    answer: str = "Comunícate con un canal oficial para bloquear la tarjeta.",
    keywords: str = '["bloqueo de tarjeta", "tarjeta perdida", "canal oficial", "2"]',
    synonyms: str = "[]",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=faq_id,
        version=1,
        is_active=True,
        status="PUBLISHED",
        published_at=None,
        unpublished_at=None,
        title=title,
        question=question,
        summary=summary,
        answer=answer,
        keywords=keywords,
        tags="[]",
        synonyms=synonyms,
        intent=None,
        priority=1,
        display_order=1,
        category_id="category-cards",
        source_url="https://www.bancognb.com.pe/inicio/servicio-cliente/reclamaciones-consulta-24hr",
        source_verified_at=datetime.now(UTC),
    )


def match(item: SimpleNamespace, score: float = 0.95) -> SimpleNamespace:
    return SimpleNamespace(faq=item, score=score, category_name="TARJETAS")


def service_for(item: SimpleNamespace) -> ChatbotService:
    return ChatbotService(
        conversation_repository=StaticConversationRepository(),
        faq_repository=StaticFAQRepository(item),
        ai_provider=NoopAIProvider(),
    )


def rag_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "chatbot_ai_enabled": True,
        "chatbot_ai_prompt_version": "scope-test-v1",
        "chatbot_ai_max_context_chars": 6000,
        "chatbot_ai_max_documents": 3,
        "chatbot_ai_min_confidence": 0.70,
        "chatbot_ai_requests_per_minute": 5,
        "chatbot_ai_max_responses_per_conversation": 3,
        "chatbot_ai_max_provider_calls_per_actor_per_hour": 20,
        "chatbot_ai_idempotency_window_seconds": 30,
        "chatbot_ai_timeout_seconds": 1,
        "chatbot_ai_max_output_chars": 1200,
        "chatbot_ai_max_output_tokens": 384,
        "chatbot_ai_daily_budget_usd": 0.50,
        "chatbot_ai_max_consecutive_failures": 3,
        "chatbot_ai_cooldown_seconds": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "query",
    [
        "2+2",
        "¿Cuánto es 2 + 2?",
        "¿Cuánto da 2+2?",
        "¿Qué clima habrá mañana?",
        "Dime un chiste de tarjeta",
        "¿Cuál es la capital de Francia?",
    ],
)
def test_out_of_scope_and_numeric_questions_do_not_get_high_confidence_faqs(
    query: str,
) -> None:
    matches = service_for(faq())._find_matches(None, query)

    assert all(item.score < 0.70 for item in matches)


def test_single_generic_word_is_not_enough_to_select_a_faq() -> None:
    matches = service_for(faq())._find_matches(None, "tarjeta")

    assert matches == []


@pytest.mark.parametrize(
    "message",
    [
        "Quiero ver cómo va mi reclamo",
        "Hace días mandé una solicitud y nadie responde",
        "No recuerdo el código de mi caso",
        "¿Dónde veo las respuestas del asesor?",
    ],
)
def test_private_ticket_language_is_recognized_before_ai(message: str) -> None:
    assert ChatbotService._ticket_query_request(message) is not None


def test_reporting_an_error_starts_the_existing_ticket_draft_flow() -> None:
    assert ChatbotService._is_ticket_request("Necesito reportar un error")
    assert not ChatbotService._is_ticket_request("Tengo un problema y quisiera ayuda")


def test_natural_advisor_request_and_navigation_phrases_are_deterministic() -> None:
    assert ChatbotService._is_advisor_request("Quiero conversar con alguien")
    assert (
        ChatbotService._conversation_lifecycle_action("Regresa al inicio") is not None
    )


def test_unhelpful_rephrase_is_clarified_without_escalating() -> None:
    assert ChatbotService._is_unhelpful_follow_up(
        "Eso no era lo que estaba preguntando"
    )


@pytest.mark.parametrize("expression", ["2+2", "2 × 2", "2 - 2", "2/2"])
def test_arithmetic_expression_is_recognized_as_non_banking_content(
    expression: str,
) -> None:
    assert ChatbotService._contains_arithmetic_expression(expression)


def test_ticket_tracking_code_is_not_treated_as_arithmetic() -> None:
    assert not ChatbotService._contains_arithmetic_expression("TCK-20261015-620F8AF81E")


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("mi contraseña es Secreta-123", "Secreta-123"),
        ("PIN: 4821", "4821"),
        ("OTP 654321", "654321"),
        ("CVV 987", "987"),
        ("token=abc.def.ghi", "abc.def.ghi"),
        ("api_key: sk-proyecto-secreto", "sk-proyecto-secreto"),
        ("tarjeta 4111 1111 1111 1111", "4111 1111 1111 1111"),
        ("cliente@example.com", "cliente@example.com"),
    ],
)
def test_minimize_provider_input_redacts_credentials_and_pii(
    message: str, secret: str
) -> None:
    minimized = minimize_provider_input(message)

    assert secret not in minimized
    assert "[dato-sensible-redactado]" in minimized


def test_minimize_provider_input_preserves_non_sensitive_intent() -> None:
    message = "Necesito bloquear mi tarjeta"

    assert minimize_provider_input(message) == message


def test_contextual_provider_input_is_redacted_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.chatbot_service.get_settings",
        lambda: SimpleNamespace(chatbot_max_message_length=180),
    )

    value = ChatbotService._contextual_provider_input(
        "Mi PIN es 4821 y " + "a" * 300,
        "La respuesta anterior fue extensa " + "b" * 1200,
        "Mi tarjeta es 4111 1111 1111 1111",
    )

    assert len(value) <= 180
    assert "4821" not in value
    assert "4111" not in value


def test_specific_banking_question_still_matches_verified_faq() -> None:
    matches = service_for(faq())._find_matches(
        None, "¿Cómo bloqueo una tarjeta perdida?"
    )

    assert matches
    assert matches[0].faq.id == "faq-card-block"
    assert matches[0].score >= 0.70


@pytest.mark.parametrize(
    "query",
    [
        "¿Cómo blokear una tarjeta perdida?",
        "perdí mi tarjeta, ¿cómo la bloqueo?",
        "necesito inhabilitar mi plástico extraviado",
    ],
)
def test_retrieval_tolerates_typo_and_verified_paraphrases(query: str) -> None:
    item = faq(synonyms='["inhabilitar plástico", "plástico extraviado"]')

    matches = service_for(item)._find_matches(None, query)

    assert matches
    assert matches[0].faq.id == item.id
    assert matches[0].score >= 0.70


@pytest.mark.parametrize(
    "query",
    ["tarjeta de cumpleaños", "programar una tarjeta web", "dime un chiste de tarjeta"],
)
def test_retrieval_rejects_unrelated_shared_product_words(query: str) -> None:
    matches = service_for(faq())._find_matches(None, query)

    assert all(item.score < 0.70 for item in matches)


def test_rag_does_not_call_provider_when_sources_are_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CountingProvider()
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    result = rag.generate(
        conversation_id="scope-conversation",
        user_message="Dime un chiste de tarjeta",
        matches=[match(faq(), score=0.50)],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert not result.accepted
    assert provider.calls == 0


def test_rag_does_not_call_provider_for_ambiguous_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CountingProvider()
    second_faq = faq(
        faq_id="faq-card-block-alt",
        question="¿Cómo bloquear una tarjeta extraviada?",
        title="Bloqueo de tarjeta extraviada",
        answer="Usa un canal oficial para bloquear una tarjeta extraviada.",
    )
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    result = rag.generate(
        conversation_id="ambiguous-conversation",
        user_message="¿Cómo bloqueo mi tarjeta?",
        matches=[match(faq(), 0.93), match(second_faq, 0.90)],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert not result.accepted
    assert result.reason == "AMBIGUOUS_CONTEXT"
    assert provider.calls == 0


def test_conversational_mode_calls_provider_without_rag_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CapturingProvider(
        AIGenerationResult.generated(
            text="Entiendo. ¿Qué parte de la respuesta no te ayudó?",
            source_ids=[],
            provider="test-provider",
            model="test-model",
        )
    )
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    outcome = rag.generate(
        conversation_id="conversation-meta",
        user_message="no me ayudó",
        matches=[],
        requests_last_minute=0,
        responses_in_conversation=0,
        grounding_required=False,
    )

    assert outcome.accepted
    assert outcome.sources == ()
    assert provider.requests[0].grounding_required is False
    assert provider.requests[0].context == ()


@pytest.mark.parametrize(
    "text",
    [
        "La tasa es 15%.",
        "Debes presentar dos documentos.",
        "Ingresa a https://ejemplo.test para bloquear la tarjeta.",
    ],
)
def test_conversational_mode_rejects_banking_claims_without_sources(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CapturingProvider(
        AIGenerationResult.generated(
            text=text,
            source_ids=[],
            provider="test-provider",
            model="test-model",
        )
    )
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    outcome = rag.generate(
        conversation_id="conversation-unsafe-meta",
        user_message="no me ayudó",
        matches=[],
        requests_last_minute=0,
        responses_in_conversation=0,
        grounding_required=False,
    )

    assert not outcome.accepted
    assert outcome.reason == "UNGROUNDED_CLAIM"
    assert len(provider.requests) == 1


def test_rag_trace_records_provider_token_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CountingProvider(
        AIGenerationResult.generated(
            text="Comunícate con un canal oficial para bloquear la tarjeta.",
            source_ids=["faq-card-block"],
            provider="test-provider",
            model="gpt-5.4-mini",
            input_tokens=81,
            output_tokens=24,
            total_tokens=105,
            intent_classification="FAQ_QUERY",
        )
    )
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    result = rag.generate(
        conversation_id="usage-conversation",
        user_message="¿Cómo bloqueo una tarjeta perdida?",
        matches=[match(faq())],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert result.accepted
    assert result.trace["input_tokens"] == 81
    assert result.trace["output_tokens"] == 24
    assert result.trace["total_tokens"] == 105
    assert result.trace["intent_classification"] == "FAQ_QUERY"
    assert result.trace["estimated_cost_usd"] == pytest.approx(0.00016875)


def test_rag_rejects_control_intent_even_when_answer_has_valid_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    provider = CapturingProvider(
        AIGenerationResult.generated(
            text="Comunícate con un canal oficial para bloquear la tarjeta.",
            source_ids=["faq-card-block"],
            provider="test-provider",
            model="test-model",
            intent_classification="TICKET_QUERY",
        )
    )
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    outcome = rag.generate(
        conversation_id="classification-conversation",
        user_message="¿Cómo bloqueo una tarjeta perdida?",
        matches=[match(faq())],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert not outcome.accepted
    assert outcome.reason == "INTENT_NOT_ALLOWED"


def test_rag_does_not_call_provider_when_daily_cost_budget_would_be_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = rag_settings(chatbot_ai_daily_budget_usd=0.0001)
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: settings,
    )
    provider = CapturingProvider(
        AIGenerationResult.generated(
            text="Comunícate con un canal oficial para bloquear la tarjeta.",
            source_ids=["faq-card-block"],
            provider="openai",
            model="gpt-5.4-mini",
            intent_classification="FAQ_QUERY",
        )
    )
    provider.model = "gpt-5.4-mini"
    rag = RAGService(provider=provider, runtime=_RuntimeState())

    outcome = rag.generate(
        conversation_id="budget-conversation",
        user_message="¿Cómo bloqueo una tarjeta perdida?",
        matches=[match(faq())],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert not outcome.accepted
    assert outcome.reason == "DAILY_BUDGET_LIMIT"
    assert provider.requests == []


def test_output_rejects_an_ungrounded_extra_sentence() -> None:
    source = faq()
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "Comunícate con un canal oficial para bloquear la tarjeta. Además, 2 + 2 es 4.",
        context,
        (source.id,),
        1200,
    )

    assert not decision.allowed
    assert decision.reason == "OUT_OF_CONTEXT"


def test_output_rejects_numeric_claims_not_present_in_sources() -> None:
    source = faq()
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "Comunícate con un canal oficial para bloquear la tarjeta. La tasa es 25%.",
        context,
        (source.id,),
        1200,
    )

    assert not decision.allowed
    assert decision.reason == "OUT_OF_CONTEXT"


def test_output_rejects_an_unsupported_non_numeric_sentence() -> None:
    source = faq()
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "Comunícate con un canal oficial para bloquear la tarjeta. "
        "La capital de Francia es París.",
        context,
        (source.id,),
        1200,
    )

    assert not decision.allowed
    assert decision.reason == "OUT_OF_CONTEXT"


def test_output_accepts_a_number_present_in_the_verified_source() -> None:
    source = faq(answer="Comunícate con un canal oficial durante 24 horas.")
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "Comunícate con un canal oficial durante 24 horas.",
        context,
        (source.id,),
        1200,
    )

    assert decision.allowed


def test_output_accepts_numbered_step_marker_without_treating_it_as_a_claim() -> None:
    source = faq()
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "1. Comunícate con un canal oficial para bloquear la tarjeta.",
        context,
        (source.id,),
        1200,
    )

    assert decision.allowed


def test_output_accepts_answer_supported_by_the_source() -> None:
    source = faq()
    context = RAGService(runtime=_RuntimeState()).build_context([match(source)])

    decision = validate_output(
        "Comunícate con un canal oficial para bloquear la tarjeta.",
        context,
        (source.id,),
        1200,
    )

    assert decision.allowed


def test_rag_accepts_a_supported_faq_paraphrase_from_a_simulated_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    source = faq(
        question="¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?",
        title="Compras por internet en tarjetas",
        summary=(
            "Los servicios adicionales de tarjetas, como compras por internet, "
            "pueden configurarse desde los canales digitales."
        ),
        answer=(
            "Si no tienes acceso, consulta las opciones de atención publicadas "
            "por el Banco."
        ),
    )
    provider = CountingProvider(
        AIGenerationResult.generated(
            text="El servicio puede modificarse mediante plataformas virtuales.",
            source_ids=[source.id],
            provider="test-provider",
            model="test-model",
        )
    )

    outcome = RAGService(provider=provider, runtime=_RuntimeState()).generate(
        conversation_id="supported-paraphrase",
        user_message=source.question,
        matches=[match(source)],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert provider.calls == 1
    assert outcome.accepted
    assert outcome.answer == (
        "El servicio puede modificarse mediante plataformas virtuales."
    )
    assert outcome.sources[0].source_id == source.id
    assert outcome.trace["provider_status"] == "GENERATED"
    assert outcome.trace["validated"] is True
    assert outcome.trace["fallback"] is False
    assert outcome.trace.get("fallback_reason") is None


@pytest.mark.parametrize(
    "unsupported_claim",
    [
        "el Banco aprobará automáticamente cada compra",
        "el Banco cobrará una comisión adicional",
        "debes presentar otro documento",
        "el servicio se aplicará exclusivamente a tarjetas activas",
    ],
)
def test_rag_rejects_a_supported_statement_mixed_with_an_unsupported_bank_claim(
    monkeypatch: pytest.MonkeyPatch,
    unsupported_claim: str,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    source = faq(
        question="¿Puedo habilitar o deshabilitar compras por internet en mi tarjeta?",
        title="Compras por internet en tarjetas",
        summary=(
            "Los servicios adicionales de tarjetas, como compras por internet, "
            "pueden configurarse desde los canales digitales."
        ),
        answer=(
            "Si no tienes acceso, consulta las opciones de atención publicadas "
            "por el Banco."
        ),
    )
    provider = CountingProvider(
        AIGenerationResult.generated(
            text=(
                "Las compras por internet pueden configurarse desde canales "
                f"digitales y {unsupported_claim}."
            ),
            source_ids=[source.id],
            provider="test-provider",
            model="test-model",
        )
    )

    outcome = RAGService(provider=provider, runtime=_RuntimeState()).generate(
        conversation_id="mixed-unsupported-claim",
        user_message=source.question,
        matches=[match(source)],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert provider.calls == 1
    assert not outcome.accepted
    assert outcome.reason == "OUT_OF_CONTEXT"
    assert outcome.trace["provider_status"] == "GENERATED"
    assert outcome.trace["validated"] is False
    assert outcome.trace["validation_reason"] == "OUT_OF_CONTEXT"
    assert outcome.trace["fallback"] is True
    assert outcome.trace["fallback_reason"] == "OUT_OF_CONTEXT"


def test_rag_marks_generated_but_rejected_output_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: rag_settings(),
    )
    source = faq()
    provider = CountingProvider(
        AIGenerationResult.generated(
            text="La tasa del producto es 15%.",
            source_ids=[source.id],
            provider="test-provider",
            model="test-model",
        )
    )

    outcome = RAGService(provider=provider, runtime=_RuntimeState()).generate(
        conversation_id="generated-rejected-trace",
        user_message="¿Cómo bloqueo una tarjeta perdida?",
        matches=[match(source)],
        requests_last_minute=0,
        responses_in_conversation=0,
    )

    assert not outcome.accepted
    assert outcome.reason == "OUT_OF_CONTEXT"
    assert outcome.trace["provider_status"] == "GENERATED"
    assert outcome.trace["validated"] is False
    assert outcome.trace["fallback"] is True
    assert outcome.trace["fallback_reason"] == "OUT_OF_CONTEXT"
