from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.chatbot.services.ai_provider import AIGenerationResult, NoopAIProvider
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.guardrails import validate_output
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


def faq(
    *,
    faq_id: str = "faq-card-block",
    question: str = "¿Cómo bloqueo una tarjeta perdida?",
    title: str = "Bloqueo de tarjeta",
    summary: str = "Bloquea la tarjeta perdida desde un canal oficial.",
    answer: str = "Comunícate con un canal oficial para bloquear la tarjeta.",
    keywords: str = '["bloqueo de tarjeta", "tarjeta perdida", "canal oficial", "2"]',
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
        synonyms="[]",
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


@pytest.mark.parametrize("expression", ["2+2", "2 × 2", "2 - 2", "2/2"])
def test_arithmetic_expression_is_recognized_as_non_banking_content(
    expression: str,
) -> None:
    assert ChatbotService._contains_arithmetic_expression(expression)


def test_ticket_tracking_code_is_not_treated_as_arithmetic() -> None:
    assert not ChatbotService._contains_arithmetic_expression("TCK-20261015-620F8AF81E")


def test_specific_banking_question_still_matches_verified_faq() -> None:
    matches = service_for(faq())._find_matches(
        None, "¿Cómo bloqueo una tarjeta perdida?"
    )

    assert matches
    assert matches[0].faq.id == "faq-card-block"
    assert matches[0].score >= 0.70


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
            model="test-model",
            input_tokens=81,
            output_tokens=24,
            total_tokens=105,
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
