import json
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace

from app.modules.chatbot.services.ai_provider import NoopAIProvider
from app.modules.chatbot.services.chatbot_service import ChatbotService
from app.modules.chatbot.services.rag_service import RAGService, _RuntimeState


class StaticFAQRepository:
    def __init__(self, items: list[SimpleNamespace]) -> None:
        self.items = items

    def list_active(self, _session: object) -> list[SimpleNamespace]:
        return self.items


class StaticConversationRepository:
    @staticmethod
    def category_name(_session: object, _category_id: str) -> str:
        return "TARJETAS"


class ScenarioEmbeddings:
    model = "text-embedding-3-small"

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def embed(self, inputs: list[str], *, timeout_seconds: float):
        self.inputs.append(inputs)
        vectors = [
            [1.0, 0.0]
            if index == 0 or "semantic_target_marker" in text.casefold()
            else [0.0, 1.0]
            for index, text in enumerate(inputs)
        ]
        from app.modules.chatbot.services.embedding_provider import (
            EmbeddingGenerationResult,
        )

        return EmbeddingGenerationResult.generated(
            vectors=vectors,
            provider="scenario-double",
            model=self.model,
            input_tokens=16,
        )


def faq(faq_id: str, question: str, summary: str, answer: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=faq_id,
        version=1,
        status="PUBLISHED",
        is_active=True,
        published_at=None,
        unpublished_at=None,
        title=question,
        question=question,
        summary=summary,
        answer=answer,
        keywords="bloquear, tarjeta, perdida",
        tags="[]",
        synonyms="[]",
        intent=None,
        priority=1,
        display_order=1,
        category_id="cards",
        source_url="https://www.bancognb.com.pe/inicio/banca-personas/tarjetas",
        source_verified_at=datetime.now(UTC),
    )


def test_reproducible_scenarios_compare_lexical_and_hybrid_recall_at_one(
    monkeypatch,
):
    settings = SimpleNamespace(
        chatbot_ai_enabled=True,
        chatbot_ai_embeddings_enabled=True,
        chatbot_ai_embedding_model="text-embedding-3-small",
        chatbot_ai_embedding_dimensions=2,
        chatbot_ai_embedding_max_documents=20,
        chatbot_ai_max_responses_per_conversation=3,
        chatbot_ai_requests_per_minute=5,
        chatbot_ai_max_provider_calls_per_actor_per_hour=20,
        chatbot_ai_daily_budget_usd=0.50,
        chatbot_ai_timeout_seconds=3,
        chatbot_ai_max_consecutive_failures=3,
        chatbot_ai_cooldown_seconds=60,
        chatbot_ai_high_confidence_threshold=0.70,
        chatbot_high_confidence_threshold=0.70,
        chatbot_medium_confidence_threshold=0.35,
    )
    monkeypatch.setattr(
        "app.modules.chatbot.services.chatbot_service.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "app.modules.chatbot.services.rag_service.get_settings",
        lambda: settings,
    )
    target = faq(
        "faq-lost-card",
        "Como bloquear una tarjeta perdida",
        "semantic_target_marker bloquear tarjeta perdida",
        "Usa un canal oficial para bloquear la tarjeta perdida.",
    )
    unrelated = faq(
        "faq-account",
        "Como abrir una cuenta",
        "Apertura de cuenta en canales oficiales.",
        "Consulta los canales oficiales para abrir una cuenta.",
    )
    chat = ChatbotService(
        conversation_repository=StaticConversationRepository(),
        faq_repository=StaticFAQRepository([target, unrelated]),
        ai_provider=NoopAIProvider(),
    )
    embeddings = ScenarioEmbeddings()
    rag = RAGService(
        provider=NoopAIProvider(),
        embedding_provider=embeddings,
        runtime=_RuntimeState(),
    )
    scenarios = (
        ("faq_exact", "Como bloqueo tarjeta perdida", True),
        ("faq_paraphrase", "Se me extravio el plastico", False),
    )
    baseline_hits = 0
    hybrid_hits = 0
    latencies_ms: list[float] = []
    reported_embedding_tokens = 0
    estimated_embedding_cost_usd = 0.0
    embedding_cache_hits = 0

    for scenario_id, query, lexical_expected in scenarios:
        started = perf_counter()
        lexical = chat._find_matches(None, query)
        candidates = chat._retrieval_candidates(None, lexical)
        hybrid, trace = rag.retrieve_hybrid(
            user_message=query,
            lexical_matches=lexical,
            candidates=candidates,
            requests_last_minute=0,
            responses_in_conversation=0,
        )
        latencies_ms.append((perf_counter() - started) * 1000)
        baseline_top = lexical[0].faq.id if lexical else None
        hybrid_top = hybrid[0].faq.id if hybrid else None
        baseline_hits += baseline_top == target.id
        hybrid_hits += hybrid_top == target.id
        reported_embedding_tokens += int(trace["embedding_input_tokens"])
        estimated_embedding_cost_usd += float(trace["embedding_estimated_cost_usd"])
        embedding_cache_hits += int(trace["embedding_cache_hits"])

        assert bool(lexical) is lexical_expected, scenario_id
        assert trace["retrieval_mode"] == "hybrid"
        assert hybrid_top == target.id, scenario_id

    metrics = {
        "scenario_count": len(scenarios),
        "lexical_recall_at_1": baseline_hits / len(scenarios),
        "hybrid_recall_at_1": hybrid_hits / len(scenarios),
        "embedding_api_calls": len(embeddings.inputs),
        "embedding_inputs_total": sum(len(call) for call in embeddings.inputs),
        "embedding_input_tokens_reported_by_double": reported_embedding_tokens,
        "embedding_cache_hits": embedding_cache_hits,
        "estimated_embedding_cost_usd": round(estimated_embedding_cost_usd, 8),
        "retrieval_latency_ms_mean": sum(latencies_ms) / len(latencies_ms),
    }

    print("CHATBOT_HYBRID_EVALUATION=" + json.dumps(metrics, sort_keys=True))
    assert metrics["lexical_recall_at_1"] == 0.5, metrics
    assert metrics["hybrid_recall_at_1"] == 1.0, metrics
    assert metrics["embedding_api_calls"] == 2, metrics
    assert metrics["embedding_inputs_total"] == 4, metrics
    assert metrics["embedding_input_tokens_reported_by_double"] == 32, metrics
    assert metrics["embedding_cache_hits"] == 2, metrics
    assert metrics["estimated_embedding_cost_usd"] == 0.00000064, metrics
    assert metrics["retrieval_latency_ms_mean"] >= 0, metrics
