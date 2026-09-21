import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class KnowledgeContext:
    source_id: str
    version: int
    title: str
    category: str | None
    content: str
    score: float
    status: str
    valid_from: str | None
    valid_to: str | None


@dataclass(frozen=True)
class AIProviderRequest:
    user_message: str
    context: tuple[KnowledgeContext, ...]
    policy: str
    prompt_version: str
    max_output_chars: int
    timeout_seconds: float


@dataclass(frozen=True)
class AIGenerationResult:
    status: str
    text: str | None
    source_ids: tuple[str, ...]
    provider: str
    model: str | None
    latency_ms: int | None = None
    error_code: str | None = None

    @classmethod
    def generated(
        cls,
        *,
        text: str,
        source_ids: list[str] | tuple[str, ...],
        provider: str,
        model: str | None,
        latency_ms: int | None = None,
    ) -> "AIGenerationResult":
        return cls(
            status="GENERATED",
            text=text,
            source_ids=tuple(source_ids),
            provider=provider,
            model=model,
            latency_ms=latency_ms,
        )

    @classmethod
    def failed(
        cls,
        *,
        status: str,
        provider: str,
        model: str | None,
        error_code: str,
        latency_ms: int | None = None,
    ) -> "AIGenerationResult":
        return cls(
            status=status,
            text=None,
            source_ids=(),
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            error_code=error_code,
        )


class AIProvider(Protocol):
    name: str
    model: str | None

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        """Generate only from the supplied, already-authorized context."""


class NoopAIProvider:
    def __init__(self, name: str = "noop", reason: str = "provider_disabled") -> None:
        self.name = name
        self.reason = reason
        self.model = None

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        return AIGenerationResult.failed(
            status="UNAVAILABLE",
            provider=self.name,
            model=self.model,
            error_code=self.reason,
        )


class HttpJsonAIProvider:
    """Optional adapter for an approved internal JSON AI gateway.

    The gateway contract is intentionally small and provider-neutral:
    request fields are ``model``, ``input``, ``policy``, ``prompt_version`` and
    authorized ``context``; the response must contain ``text`` and ``source_ids``.
    """

    name = "http_json"

    def __init__(self, endpoint: str, api_key: str, model: str) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or parsed.username:
            raise ValueError("El endpoint IA no es válido")
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        payload = {
            "model": self.model,
            "input": request.user_message,
            "policy": request.policy,
            "prompt_version": request.prompt_version,
            "context": [
                {
                    "source_id": chunk.source_id,
                    "version": chunk.version,
                    "title": chunk.title,
                    "category": chunk.category,
                    "content": chunk.content,
                    "score": chunk.score,
                    "status": chunk.status,
                    "valid_from": chunk.valid_from,
                    "valid_to": chunk.valid_to,
                }
                for chunk in request.context
            ],
        }
        http_request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=request.timeout_seconds) as response:
                body = response.read(1_000_000)
            decoded = json.loads(body.decode("utf-8"))
            text = decoded.get("text")
            source_ids = decoded.get("source_ids")
            if not isinstance(text, str) or not isinstance(source_ids, list):
                return AIGenerationResult.failed(
                    status="ERROR",
                    provider=self.name,
                    model=self.model,
                    error_code="invalid_provider_response",
                )
            return AIGenerationResult.generated(
                text=text,
                source_ids=[str(source_id) for source_id in source_ids],
                provider=self.name,
                model=self.model,
            )
        except TimeoutError:
            return AIGenerationResult.failed(
                status="TIMEOUT",
                provider=self.name,
                model=self.model,
                error_code="provider_timeout",
            )
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return AIGenerationResult.failed(
                status="ERROR",
                provider=self.name,
                model=self.model,
                error_code="provider_error",
            )


def build_ai_provider() -> AIProvider:
    """Return the safe local provider until an approved adapter is configured."""
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.chatbot_ai_enabled:
        return NoopAIProvider()
    if (
        settings.chatbot_ai_provider.casefold() == "http_json"
        and settings.chatbot_ai_endpoint
        and settings.chatbot_ai_api_key
    ):
        try:
            return HttpJsonAIProvider(
                endpoint=settings.chatbot_ai_endpoint,
                api_key=settings.chatbot_ai_api_key,
                model=settings.chatbot_ai_model,
            )
        except ValueError:
            pass
    return NoopAIProvider(
        name=settings.chatbot_ai_provider,
        reason="provider_not_configured",
    )
