from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import perf_counter
from typing import Protocol

from app.core.config import get_settings
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProvider,
    AIProviderRequest,
    KnowledgeContext,
    build_ai_provider,
)
from app.modules.chatbot.services.guardrails import (
    GUARDRAIL_POLICY,
    detect_prompt_injection,
    detect_sensitive_request,
    minimize_provider_input,
    validate_context,
    validate_output,
)
from app.shared.text import normalize_text


class MatchLike(Protocol):
    faq: object
    score: float
    category_name: str | None


@dataclass(frozen=True)
class AIOutcome:
    accepted: bool
    answer: str | None
    sources: tuple[KnowledgeContext, ...]
    trace: dict[str, object]
    reason: str


@dataclass
class _RuntimeState:
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None
    recent_results: dict[str, tuple[datetime, AIOutcome]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_RUNTIME = _RuntimeState()


class RAGService:
    def __init__(
        self,
        provider: AIProvider | None = None,
        runtime: _RuntimeState | None = None,
    ) -> None:
        self.provider = provider or build_ai_provider()
        self.runtime = runtime or _RUNTIME

    def generate(
        self,
        *,
        conversation_id: str,
        user_message: str,
        matches: list[MatchLike],
        requests_last_minute: int,
        responses_in_conversation: int,
    ) -> AIOutcome:
        settings = get_settings()
        context = self.build_context(matches)
        cache_key = self._cache_key(conversation_id, user_message, context)
        base_trace: dict[str, object] = {
            "provider": getattr(self.provider, "name", "unknown"),
            "model": getattr(self.provider, "model", None),
            "prompt_version": settings.chatbot_ai_prompt_version,
            "source_ids": [chunk.source_id for chunk in context],
            "source_versions": [chunk.version for chunk in context],
            "scores": [chunk.score for chunk in context],
            "fallback": True,
        }
        if not settings.chatbot_ai_enabled:
            return self._fallback(base_trace, "DISABLED")
        if detect_prompt_injection(user_message):
            return self._fallback(base_trace, "USER_INJECTION")
        if detect_sensitive_request(user_message):
            return self._fallback(base_trace, "SENSITIVE_REQUEST")
        context_decision = validate_context(context)
        if not context_decision.allowed:
            return self._fallback(base_trace, context_decision.reason or "CONTEXT")
        if (
            responses_in_conversation
            >= settings.chatbot_ai_max_responses_per_conversation
        ):
            return self._fallback(base_trace, "CONVERSATION_LIMIT")
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        if requests_last_minute >= settings.chatbot_ai_requests_per_minute:
            return self._fallback(base_trace, "RATE_LIMIT")
        if self._circuit_open():
            return self._fallback(base_trace, "CIRCUIT_OPEN")

        request = AIProviderRequest(
            user_message=minimize_provider_input(user_message),
            context=tuple(context),
            policy=GUARDRAIL_POLICY,
            prompt_version=settings.chatbot_ai_prompt_version,
            max_output_chars=settings.chatbot_ai_max_output_chars,
            timeout_seconds=settings.chatbot_ai_timeout_seconds,
        )
        result = self._call_provider(request)
        trace = {
            **base_trace,
            "provider": result.provider,
            "model": result.model,
            "duration_ms": result.latency_ms,
            "provider_status": result.status,
            "error_code": result.error_code,
            "fallback": result.status != "GENERATED",
        }
        if result.status != "GENERATED":
            if result.status in {"ERROR", "TIMEOUT"}:
                self._record_failure()
            return self._fallback(trace, result.status)
        self._record_success()
        decision = validate_output(
            result.text,
            context,
            result.source_ids,
            settings.chatbot_ai_max_output_chars,
        )
        trace["validated"] = decision.allowed
        trace["validation_reason"] = decision.reason
        if not decision.allowed:
            return self._fallback(trace, decision.reason or "GUARDRAIL")
        selected_sources = tuple(
            chunk for chunk in context if chunk.source_id in set(result.source_ids)
        )
        trace["fallback"] = False
        outcome = AIOutcome(
            True, result.text.strip(), selected_sources, trace, "ACCEPTED"
        )
        self._cache(cache_key, outcome)
        return outcome

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
    ) -> str:
        sources = ",".join(f"{chunk.source_id}:{chunk.version}" for chunk in context)
        return f"{conversation_id}:{normalize_text(user_message)}:{sources}"

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
        trace["fallback_reason"] = reason
        return AIOutcome(False, None, (), trace, reason)


__all__ = ["AIOutcome", "RAGService"]
