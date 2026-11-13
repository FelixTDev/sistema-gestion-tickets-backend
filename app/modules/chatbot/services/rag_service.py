from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from math import ceil
from threading import Lock
from time import perf_counter
from typing import Protocol

from app.core.config import get_settings
from app.modules.chatbot.schemas.chatbot import ChatbotIntent
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProvider,
    AIProviderRequest,
    KnowledgeContext,
    build_ai_provider,
)
from app.modules.chatbot.services.embedding_provider import (
    EmbeddingGenerationResult,
    EmbeddingProvider,
    build_embedding_provider,
)
from app.modules.chatbot.services.guardrails import (
    GUARDRAIL_POLICY,
    detect_prompt_injection,
    detect_sensitive_request,
    has_verified_official_source,
    minimize_provider_input,
    validate_context,
    validate_conversational_output,
    validate_output,
)
from app.shared.text import normalize_text


class MatchLike(Protocol):
    faq: object
    score: float
    category_name: str | None


@dataclass(frozen=True)
class _RankedMatch:
    faq: object
    score: float
    intent: str | None
    category_name: str | None


@dataclass(frozen=True)
class AIOutcome:
    accepted: bool
    answer: str | None
    sources: tuple[KnowledgeContext, ...]
    trace: dict[str, object]
    reason: str
    intent_classification: str | None = None


@dataclass
class _RuntimeState:
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    recent_results: dict[str, tuple[datetime, AIOutcome]] = field(default_factory=dict)
    daily_budget_date: date | None = None
    daily_budget_reserved_usd: float = 0.0
    document_embeddings: dict[str, tuple[float, ...]] = field(default_factory=dict)
    query_embeddings: dict[str, tuple[float, ...]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_RUNTIME = _RuntimeState()
_EMBEDDING_PRICE_PER_MILLION_TOKENS = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
}


class RAGService:
    def __init__(
        self,
        provider: AIProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        runtime: _RuntimeState | None = None,
    ) -> None:
        self.provider = provider or build_ai_provider()
        self.embedding_provider = embedding_provider or build_embedding_provider()
        self.runtime = runtime or _RUNTIME

    def retrieve_hybrid(
        self,
        *,
        user_message: str,
        lexical_matches: list[MatchLike],
        candidates: list[MatchLike],
        requests_last_minute: int,
        responses_in_conversation: int,
        requests_by_actor_last_hour: int = 0,
    ) -> tuple[list[MatchLike], dict[str, object]]:
        settings = get_settings()
        lexical_trace: dict[str, object] = {
            "retrieval_mode": "lexical",
            "embedding_provider_called": False,
            "lexical_top_source_id": (
                getattr(lexical_matches[0].faq, "id", None) if lexical_matches else None
            ),
        }
        if (
            not settings.chatbot_ai_enabled
            or not settings.chatbot_ai_embeddings_enabled
            or self.embedding_provider is None
            or not candidates
            or detect_prompt_injection(user_message)
            or detect_sensitive_request(user_message)
            or responses_in_conversation
            >= settings.chatbot_ai_max_responses_per_conversation
            or requests_last_minute >= settings.chatbot_ai_requests_per_minute
            or requests_by_actor_last_hour
            >= settings.chatbot_ai_max_provider_calls_per_actor_per_hour
            or self._circuit_open()
        ):
            return lexical_matches, lexical_trace

        now = datetime.now(UTC)
        eligible: list[MatchLike] = []
        for candidate in candidates:
            faq = candidate.faq
            status = getattr(faq.status, "value", faq.status)
            published_at = self._utc(getattr(faq, "published_at", None))
            unpublished_at = self._utc(getattr(faq, "unpublished_at", None))
            content = self._embedding_text(faq)
            if (
                status != "PUBLISHED"
                or not faq.is_active
                or (published_at is not None and published_at > now)
                or (unpublished_at is not None and unpublished_at <= now)
                or not has_verified_official_source(
                    getattr(faq, "source_url", None),
                    getattr(faq, "source_verified_at", None),
                )
                or detect_prompt_injection(content)
            ):
                continue
            eligible.append(candidate)
        if not eligible:
            return lexical_matches, lexical_trace

        lexical_by_id = {
            getattr(match.faq, "id", ""): match for match in lexical_matches
        }
        eligible.sort(
            key=lambda match: (
                -getattr(lexical_by_id.get(match.faq.id, match), "score", 0.0),
                -getattr(match.faq, "priority", 0),
                getattr(match.faq, "display_order", 0),
                match.faq.id,
            )
        )
        eligible = eligible[: settings.chatbot_ai_embedding_max_documents]

        minimized_query = minimize_provider_input(user_message).strip()
        if not minimized_query:
            return lexical_matches, lexical_trace
        query_cache_key = self._embedding_key(
            settings.chatbot_ai_embedding_model,
            str(settings.chatbot_ai_embedding_dimensions),
            minimized_query,
        )
        texts = [self._embedding_text(match.faq) for match in eligible]
        document_keys = [
            self._embedding_key(
                settings.chatbot_ai_embedding_model,
                f"{match.faq.id}:{match.faq.version}",
                text,
            )
            for match, text in zip(eligible, texts, strict=True)
        ]
        with self.runtime.lock:
            query_vector = self.runtime.query_embeddings.get(query_cache_key)
            document_vectors = [
                self.runtime.document_embeddings.get(key) for key in document_keys
            ]
        missing_indices = [
            index for index, vector in enumerate(document_vectors) if vector is None
        ]
        embedding_cache_hits = len(document_vectors) - len(missing_indices)
        if query_vector is not None:
            embedding_cache_hits += 1
        embedding_inputs: list[str] = []
        query_is_missing = query_vector is None
        if query_is_missing:
            embedding_inputs.append(minimized_query)
        embedding_inputs.extend(texts[index] for index in missing_indices)
        provider_called = bool(embedding_inputs)
        projected_cost = 0.0
        result: EmbeddingGenerationResult | None = None
        if provider_called:
            embedding_price = _EMBEDDING_PRICE_PER_MILLION_TOKENS.get(
                settings.chatbot_ai_embedding_model
            )
            if embedding_price is None:
                return lexical_matches, {
                    **lexical_trace,
                    "embedding_error_code": "UNPRICED_EMBEDDING_MODEL",
                }
            estimated_tokens = ceil(sum(len(text) for text in embedding_inputs) / 2)
            projected_cost = round(estimated_tokens * embedding_price / 1_000_000, 8)
            reserved, reserved_total = self._reserve_daily_budget(
                projected_cost,
                settings.chatbot_ai_daily_budget_usd,
            )
            if not reserved:
                return lexical_matches, {
                    **lexical_trace,
                    "embedding_error_code": "DAILY_BUDGET_LIMIT",
                    "embedding_budget_reserved_usd": reserved_total,
                }
            result = self._call_embedding_provider(
                embedding_inputs,
                settings.chatbot_ai_timeout_seconds,
            )
            if result.status != "GENERATED" or not self._valid_embedding_batch(
                result.vectors,
                len(embedding_inputs),
                settings.chatbot_ai_embedding_dimensions,
            ):
                return lexical_matches, {
                    **lexical_trace,
                    "embedding_provider_called": True,
                    "embedding_error_code": result.error_code
                    or "invalid_embedding_response",
                    "embedding_duration_ms": result.latency_ms,
                    "embedding_estimated_cost_usd": projected_cost,
                }
            vector_index = 0
            if query_is_missing:
                query_vector = result.vectors[vector_index]
                vector_index += 1
                self._store_embedding(
                    self.runtime.query_embeddings,
                    query_cache_key,
                    query_vector,
                    max_entries=256,
                )
            for index in missing_indices:
                vector = result.vectors[vector_index]
                vector_index += 1
                document_vectors[index] = vector
                self._store_embedding(
                    self.runtime.document_embeddings,
                    document_keys[index],
                    vector,
                    max_entries=1000,
                )
        if query_vector is None or any(vector is None for vector in document_vectors):
            return lexical_matches, lexical_trace

        ranked: list[MatchLike] = []
        semantic_scores: list[tuple[str, float]] = []
        for candidate, vector in zip(eligible, document_vectors, strict=True):
            if vector is None:
                continue
            similarity = self._cosine_similarity(query_vector, vector)
            semantic_score = max(0.0, min(1.0, (similarity - 0.45) / 0.35))
            faq_id = candidate.faq.id
            lexical_score = getattr(lexical_by_id.get(faq_id, candidate), "score", 0.0)
            score = (
                max(
                    lexical_score,
                    0.65 * lexical_score + 0.35 * semantic_score,
                )
                if lexical_score > 0
                else 0.80 * semantic_score
            )
            semantic_scores.append((faq_id, similarity))
            if score > 0:
                ranked.append(
                    _RankedMatch(
                        faq=candidate.faq,
                        score=round(score, 4),
                        intent=getattr(candidate, "intent", None),
                        category_name=getattr(candidate, "category_name", None),
                    )
                )
        ranked.sort(
            key=lambda match: (
                -match.score,
                -getattr(match.faq, "priority", 0),
                getattr(match.faq, "display_order", 0),
                match.faq.id,
            )
        )
        lexical_top = lexical_matches[0].faq.id if lexical_matches else None
        trace: dict[str, object] = {
            "retrieval_mode": "hybrid",
            "embedding_provider_called": provider_called,
            "embedding_model": getattr(
                self.embedding_provider, "model", settings.chatbot_ai_embedding_model
            ),
            "embedding_unit_price_usd_per_million_tokens": (
                _EMBEDDING_PRICE_PER_MILLION_TOKENS.get(
                    settings.chatbot_ai_embedding_model
                )
            ),
            "embedding_dimensions": settings.chatbot_ai_embedding_dimensions,
            "embedding_documents": len(eligible),
            "embedding_cache_hits": embedding_cache_hits,
            "embedding_input_tokens": result.input_tokens if result else 0,
            "embedding_duration_ms": result.latency_ms if result else 0,
            "embedding_estimated_cost_usd": (
                round(
                    result.input_tokens
                    * (
                        _EMBEDDING_PRICE_PER_MILLION_TOKENS.get(
                            settings.chatbot_ai_embedding_model
                        )
                        or 0.0
                    )
                    / 1_000_000,
                    8,
                )
                if result is not None and result.input_tokens is not None
                else projected_cost
                if provider_called
                else 0.0
            ),
            "embedding_budget_reserved_usd": projected_cost,
            "lexical_top_source_id": lexical_top,
            "semantic_top_source_id": (
                max(semantic_scores, key=lambda item: item[1])[0]
                if semantic_scores
                else None
            ),
            "hybrid_top_source_id": ranked[0].faq.id if ranked else None,
            "hybrid_top_score": ranked[0].score if ranked else None,
        }
        return ranked, trace

    def generate(
        self,
        *,
        conversation_id: str,
        user_message: str,
        matches: list[MatchLike],
        requests_last_minute: int,
        responses_in_conversation: int,
        requests_by_actor_last_hour: int = 0,
        grounding_required: bool = True,
    ) -> AIOutcome:
        settings = get_settings()
        context = self.build_context(matches) if grounding_required else []
        cache_key = self._cache_key(
            conversation_id,
            user_message,
            context,
            grounding_required=grounding_required,
        )
        base_trace: dict[str, object] = {
            "provider": getattr(self.provider, "name", "unknown"),
            "model": getattr(self.provider, "model", None),
            "prompt_version": settings.chatbot_ai_prompt_version,
            "source_ids": [chunk.source_id for chunk in context],
            "source_versions": [chunk.version for chunk in context],
            "scores": [chunk.score for chunk in context],
            "fallback": True,
            "grounding_required": grounding_required,
        }
        if not settings.chatbot_ai_enabled:
            return self._fallback(base_trace, "DISABLED")
        if detect_prompt_injection(user_message):
            return self._fallback(base_trace, "USER_INJECTION")
        if detect_sensitive_request(user_message):
            return self._fallback(base_trace, "SENSITIVE_REQUEST")
        if grounding_required:
            context_decision = validate_context(context)
            if not context_decision.allowed:
                return self._fallback(base_trace, context_decision.reason or "CONTEXT")
        if (
            grounding_required
            and len(context) > 1
            and context[0].score - context[1].score < 0.15
        ):
            return self._fallback(base_trace, "AMBIGUOUS_CONTEXT")
        cached = self._get_cached(cache_key)
        if cached is not None:
            return replace(
                cached,
                trace={
                    **cached.trace,
                    "provider_called": False,
                    "cache_hit": True,
                },
            )
        if (
            responses_in_conversation
            >= settings.chatbot_ai_max_responses_per_conversation
        ):
            return self._fallback(base_trace, "CONVERSATION_LIMIT")
        if requests_last_minute >= settings.chatbot_ai_requests_per_minute:
            return self._fallback(base_trace, "RATE_LIMIT")
        if (
            requests_by_actor_last_hour
            >= settings.chatbot_ai_max_provider_calls_per_actor_per_hour
        ):
            return self._fallback(base_trace, "ACTOR_RATE_LIMIT")
        if self._circuit_open():
            return self._fallback(base_trace, "CIRCUIT_OPEN")

        intent_instruction = (
            " La salida debe incluir intent_classification con un valor exacto del "
            "catálogo cerrado: "
            + ", ".join(intent.value for intent in ChatbotIntent)
            + ". Clasifica solo el mensaje actual; no incluyas una puntuación de "
            "confianza. Esta etiqueta nunca autoriza consultas privadas, cambios "
            "de estado ni acciones; esas reglas las aplica el backend."
        )
        generation_policy = (
            GUARDRAIL_POLICY
            if grounding_required
            else (
                GUARDRAIL_POLICY
                + " Este turno es solo conversacional: reconoce el mensaje o "
                "formula una aclaración breve. No incluyas datos, requisitos, "
                "canales ni afirmaciones sobre productos o procesos bancarios."
            )
        )
        request = AIProviderRequest(
            user_message=minimize_provider_input(user_message),
            context=tuple(context),
            policy=generation_policy + intent_instruction,
            prompt_version=settings.chatbot_ai_prompt_version,
            max_output_chars=settings.chatbot_ai_max_output_chars,
            max_output_tokens=settings.chatbot_ai_max_output_tokens,
            timeout_seconds=settings.chatbot_ai_timeout_seconds,
            grounding_required=grounding_required,
        )
        projected_cost = self._projected_cost_usd(request)
        if projected_cost is not None:
            reserved, reserved_total = self._reserve_daily_budget(
                projected_cost,
                settings.chatbot_ai_daily_budget_usd,
            )
            if not reserved:
                return self._fallback(
                    {
                        **base_trace,
                        "budget_limit_usd": settings.chatbot_ai_daily_budget_usd,
                        "budget_reserved_usd": reserved_total,
                        "estimated_request_cost_usd": projected_cost,
                    },
                    "DAILY_BUDGET_LIMIT",
                )
        else:
            reserved_total = 0.0
        result = self._call_provider(request)
        estimated_cost = self._estimate_cost_usd(
            result.model, result.input_tokens, result.output_tokens
        )
        trace = {
            **base_trace,
            "provider": result.provider,
            "model": result.model,
            "duration_ms": result.latency_ms,
            "provider_status": result.status,
            "error_code": result.error_code,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.total_tokens,
            "estimated_cost_usd": (
                estimated_cost if estimated_cost is not None else projected_cost
            ),
            "estimated_request_cost_usd": projected_cost,
            "budget_reserved_usd": reserved_total,
            "budget_limit_usd": settings.chatbot_ai_daily_budget_usd,
            "intent_classification": result.intent_classification,
            "provider_called": True,
            "cache_hit": False,
            "fallback": result.status != "GENERATED",
        }
        if result.status != "GENERATED":
            if result.status in {"ERROR", "TIMEOUT"}:
                self._record_failure()
            return self._fallback(trace, result.status)
        self._record_success()
        intent_classification = result.intent_classification or (
            ChatbotIntent.FAQ_QUERY.value
            if grounding_required
            else ChatbotIntent.CLARIFICATION.value
        )
        try:
            classified_intent = ChatbotIntent(intent_classification)
        except ValueError:
            return self._fallback(trace, "INVALID_INTENT")
        allowed_intents = (
            {ChatbotIntent.FAQ_QUERY}
            if grounding_required
            else {
                ChatbotIntent.CLARIFICATION,
                ChatbotIntent.OUT_OF_SCOPE,
                ChatbotIntent.UNKNOWN,
            }
        )
        if classified_intent not in allowed_intents:
            return self._fallback(trace, "INTENT_NOT_ALLOWED")
        decision = (
            validate_output(
                result.text,
                context,
                result.source_ids,
                settings.chatbot_ai_max_output_chars,
            )
            if grounding_required
            else validate_conversational_output(
                result.text,
                result.source_ids,
                settings.chatbot_ai_max_output_chars,
            )
        )
        trace["validated"] = decision.allowed
        trace["validation_reason"] = decision.reason
        trace["intent_classification"] = classified_intent.value
        if not decision.allowed:
            return self._fallback(trace, decision.reason or "GUARDRAIL")
        selected_sources = tuple(
            chunk for chunk in context if chunk.source_id in set(result.source_ids)
        )
        trace["fallback"] = False
        outcome = AIOutcome(
            True,
            result.text.strip(),
            selected_sources,
            trace,
            "ACCEPTED",
            classified_intent.value,
        )
        self._cache(cache_key, outcome)
        return outcome

    @staticmethod
    def _embedding_text(faq: object) -> str:
        fields = (
            (getattr(faq, "title", None), 200),
            (getattr(faq, "question", None), 700),
            (getattr(faq, "summary", None), 400),
            (getattr(faq, "keywords", None), 250),
            (getattr(faq, "tags", None), 250),
            (getattr(faq, "synonyms", None), 250),
            (getattr(faq, "answer", None), 500),
        )
        return "\n".join(
            str(value)[:limit]
            for value, limit in fields
            if isinstance(value, str) and value.strip()
        )[:2400]

    def _call_embedding_provider(
        self, inputs: list[str], timeout_seconds: float
    ) -> EmbeddingGenerationResult:
        provider = self.embedding_provider
        if provider is None:
            return EmbeddingGenerationResult.failed(
                provider="none",
                model="unknown",
                error_code="provider_not_configured",
            )
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            provider.embed,
            inputs,
            timeout_seconds=timeout_seconds,
        )
        try:
            result = future.result(timeout=timeout_seconds)
        except TimeoutError:
            future.cancel()
            return EmbeddingGenerationResult.failed(
                status="TIMEOUT",
                provider=getattr(provider, "name", "unknown"),
                model=getattr(provider, "model", "unknown"),
                error_code="provider_timeout",
            )
        except Exception:
            return EmbeddingGenerationResult.failed(
                provider=getattr(provider, "name", "unknown"),
                model=getattr(provider, "model", "unknown"),
                error_code="provider_error",
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if not isinstance(result, EmbeddingGenerationResult):
            return EmbeddingGenerationResult.failed(
                provider=getattr(provider, "name", "unknown"),
                model=getattr(provider, "model", "unknown"),
                error_code="invalid_embedding_result",
            )
        return result

    @staticmethod
    def _valid_embedding_batch(
        vectors: tuple[tuple[float, ...], ...],
        expected_count: int,
        dimensions: int,
    ) -> bool:
        from math import isfinite

        return len(vectors) == expected_count and all(
            len(vector) == dimensions
            and any(value != 0 for value in vector)
            and all(isfinite(value) for value in vector)
            for vector in vectors
        )

    @staticmethod
    def _cosine_similarity(
        first: tuple[float, ...], second: tuple[float, ...]
    ) -> float:
        if len(first) != len(second):
            return 0.0
        first_norm = sum(value * value for value in first) ** 0.5
        second_norm = sum(value * value for value in second) ** 0.5
        if first_norm == 0 or second_norm == 0:
            return 0.0
        return sum(left * right for left, right in zip(first, second, strict=True)) / (
            first_norm * second_norm
        )

    @staticmethod
    def _embedding_key(model: str, version: str, content: str) -> str:
        digest = sha256(content.encode("utf-8")).hexdigest()
        return f"{model}:{version}:{digest}"

    def _store_embedding(
        self,
        cache: dict[str, tuple[float, ...]],
        key: str,
        vector: tuple[float, ...],
        *,
        max_entries: int,
    ) -> None:
        with self.runtime.lock:
            cache[key] = vector
            while len(cache) > max_entries:
                cache.pop(next(iter(cache)))

    def build_context(self, matches: list[MatchLike]) -> list[KnowledgeContext]:
        settings = get_settings()
        context: list[KnowledgeContext] = []
        remaining = settings.chatbot_ai_max_context_chars
        for match in matches:
            faq = match.faq
            status = getattr(faq.status, "value", faq.status)
            now = datetime.now(UTC)
            published_at = self._utc(faq.published_at)
            unpublished_at = self._utc(faq.unpublished_at)
            if (
                status != "PUBLISHED"
                or not faq.is_active
                or (published_at is not None and published_at > now)
                or (unpublished_at is not None and unpublished_at <= now)
                or detect_prompt_injection(f"{faq.title} {faq.answer}")
                or not has_verified_official_source(
                    getattr(faq, "source_url", None),
                    getattr(faq, "source_verified_at", None),
                )
                or match.score < settings.chatbot_ai_min_confidence
            ):
                continue
            content = "\n".join(
                part
                for part in (faq.title, faq.question, faq.summary, faq.answer)
                if part
            )
            if remaining <= 0:
                break
            content = content[:remaining]
            context.append(
                KnowledgeContext(
                    source_id=faq.id,
                    version=faq.version,
                    title=faq.title or faq.question,
                    category=match.category_name,
                    content=content,
                    score=round(match.score, 4),
                    status=status,
                    valid_from=published_at.isoformat() if published_at else None,
                    valid_to=unpublished_at.isoformat() if unpublished_at else None,
                    source_url=getattr(faq, "source_url", None),
                    source_verified_at=(
                        self._utc(faq.source_verified_at).isoformat()
                        if getattr(faq, "source_verified_at", None)
                        else None
                    ),
                )
            )
            remaining -= len(content)
            if len(context) >= settings.chatbot_ai_max_documents:
                break
        return context

    def _call_provider(self, request: AIProviderRequest) -> AIGenerationResult:
        started = perf_counter()
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.provider.generate, request)
        try:
            result = future.result(timeout=request.timeout_seconds)
        except TimeoutError:
            future.cancel()
            return AIGenerationResult.failed(
                status="TIMEOUT",
                provider=getattr(self.provider, "name", "unknown"),
                model=getattr(self.provider, "model", None),
                error_code="provider_timeout",
            )
        except Exception:
            return AIGenerationResult.failed(
                status="ERROR",
                provider=getattr(self.provider, "name", "unknown"),
                model=getattr(self.provider, "model", None),
                error_code="provider_error",
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if not isinstance(result, AIGenerationResult):
            return AIGenerationResult.failed(
                status="ERROR",
                provider=getattr(self.provider, "name", "unknown"),
                model=getattr(self.provider, "model", None),
                error_code="invalid_provider_result",
            )
        elapsed = int((perf_counter() - started) * 1000)
        return (
            result
            if result.latency_ms is not None
            else replace(result, latency_ms=elapsed)
        )

    @staticmethod
    def _estimate_cost_usd(
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> float | None:
        if model != "gpt-5.4-mini" or input_tokens is None or output_tokens is None:
            return None
        estimate = (input_tokens * 0.75 + output_tokens * 4.50) / 1_000_000
        return round(estimate, 8)

    def _projected_cost_usd(self, request: AIProviderRequest) -> float | None:
        model = getattr(self.provider, "model", None)
        if not isinstance(model, str):
            return None
        return self._request_cost_for_model(model, request)

    def _request_cost_for_model(
        self, model: str, request: AIProviderRequest
    ) -> float | None:
        if model != "gpt-5.4-mini":
            return None
        input_characters = (
            len(request.user_message)
            + len(request.policy)
            + sum(
                len(source.title) + len(source.content) + len(source.category or "")
                for source in request.context
            )
        )
        input_tokens = ceil(input_characters / 2)
        estimate = (input_tokens * 0.75 + request.max_output_tokens * 4.50) / 1_000_000
        return round(estimate, 8)

    def _reserve_daily_budget(
        self, amount_usd: float, budget_limit_usd: float
    ) -> tuple[bool, float]:
        today = datetime.now(UTC).date()
        with self.runtime.lock:
            if self.runtime.daily_budget_date != today:
                self.runtime.daily_budget_date = today
                self.runtime.daily_budget_reserved_usd = 0.0
            next_total = self.runtime.daily_budget_reserved_usd + amount_usd
            if next_total > budget_limit_usd:
                return False, self.runtime.daily_budget_reserved_usd
            self.runtime.daily_budget_reserved_usd = next_total
            return True, next_total

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _circuit_open(self) -> bool:
        now = datetime.now(UTC)
        with self.runtime.lock:
            return bool(
                self.runtime.circuit_open_until
                and now < self.runtime.circuit_open_until
            )

    @staticmethod
    def _cache_key(
        conversation_id: str,
        user_message: str,
        context: list[KnowledgeContext],
        *,
        grounding_required: bool,
    ) -> str:
        sources = ",".join(f"{chunk.source_id}:{chunk.version}" for chunk in context)
        mode = "grounded" if grounding_required else "conversational"
        return f"{conversation_id}:{mode}:{normalize_text(user_message)}:{sources}"

    def _get_cached(self, key: str) -> AIOutcome | None:
        now = datetime.now(UTC)
        with self.runtime.lock:
            cached = self.runtime.recent_results.get(key)
            if cached is None:
                return None
            stored_at, outcome = cached
            if (
                now - stored_at
            ).total_seconds() > get_settings().chatbot_ai_idempotency_window_seconds:
                self.runtime.recent_results.pop(key, None)
                return None
            return outcome

    def _cache(self, key: str, outcome: AIOutcome) -> None:
        with self.runtime.lock:
            self.runtime.recent_results[key] = (datetime.now(UTC), outcome)

    def _record_failure(self) -> None:
        settings = get_settings()
        with self.runtime.lock:
            self.runtime.consecutive_failures += 1
            if (
                self.runtime.consecutive_failures
                >= settings.chatbot_ai_max_consecutive_failures
            ):
                self.runtime.circuit_open_until = datetime.now(UTC).replace(
                    microsecond=0
                )
                self.runtime.circuit_open_until += timedelta(
                    seconds=settings.chatbot_ai_cooldown_seconds
                )

    def _record_success(self) -> None:
        with self.runtime.lock:
            self.runtime.consecutive_failures = 0
            self.runtime.circuit_open_until = None

    @staticmethod
    def _fallback(trace: dict[str, object], reason: str) -> AIOutcome:
        trace["fallback"] = True
        trace["fallback_reason"] = reason
        return AIOutcome(False, None, (), trace, reason)


__all__ = ["AIOutcome", "RAGService"]
