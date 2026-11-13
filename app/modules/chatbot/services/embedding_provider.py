import json
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_MAX_RESPONSE_BYTES = 4_000_000


@dataclass(frozen=True)
class EmbeddingGenerationResult:
    status: str
    vectors: tuple[tuple[float, ...], ...]
    provider: str
    model: str
    input_tokens: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None

    @classmethod
    def generated(
        cls,
        *,
        vectors: list[list[float]] | tuple[tuple[float, ...], ...],
        provider: str,
        model: str,
        input_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> "EmbeddingGenerationResult":
        return cls(
            status="GENERATED",
            vectors=tuple(tuple(vector) for vector in vectors),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            latency_ms=latency_ms,
        )

    @classmethod
    def failed(
        cls,
        *,
        provider: str,
        model: str,
        error_code: str,
        status: str = "ERROR",
        latency_ms: int | None = None,
    ) -> "EmbeddingGenerationResult":
        return cls(
            status=status,
            vectors=(),
            provider=provider,
            model=model,
            error_code=error_code,
            latency_ms=latency_ms,
        )


class EmbeddingProvider(Protocol):
    name: str
    model: str

    def embed(
        self, inputs: list[str], *, timeout_seconds: float
    ) -> EmbeddingGenerationResult:
        """Create vectors for already-authorized, minimized text inputs."""


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(self, *, api_key: str, model: str, dimensions: int) -> None:
        if not api_key.strip():
            raise ValueError("La clave de OpenAI es obligatoria")
        if not model.strip():
            raise ValueError("El modelo de embeddings es obligatorio")
        if not 1 <= dimensions <= 3072:
            raise ValueError("Las dimensiones de embeddings no son válidas")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.dimensions = dimensions

    def embed(
        self, inputs: list[str], *, timeout_seconds: float
    ) -> EmbeddingGenerationResult:
        started = perf_counter()
        if not inputs or any(not text.strip() for text in inputs):
            return self._failed("invalid_embedding_input", started)
        payload = {
            "model": self.model,
            "input": inputs,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        request = Request(
            _EMBEDDINGS_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(_MAX_RESPONSE_BYTES)
        except HTTPError as error:
            return self._failed(f"embedding_http_{error.code}", started)
        except TimeoutError:
            return self._failed("provider_timeout", started, status="TIMEOUT")
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                return self._failed("provider_timeout", started, status="TIMEOUT")
            return self._failed("provider_error", started)
        except OSError:
            return self._failed("provider_error", started)

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failed("invalid_embedding_response", started)
        if not isinstance(decoded, dict):
            return self._failed("invalid_embedding_response", started)
        data = decoded.get("data")
        if not isinstance(data, list) or len(data) != len(inputs):
            return self._failed("invalid_embedding_response", started)
        ordered: list[list[float] | None] = [None] * len(inputs)
        for item in data:
            if not isinstance(item, dict):
                return self._failed("invalid_embedding_response", started)
            index = item.get("index")
            vector = item.get("embedding")
            if (
                type(index) is not int
                or not 0 <= index < len(inputs)
                or ordered[index] is not None
                or not isinstance(vector, list)
                or len(vector) != self.dimensions
                or any(
                    type(value) not in {int, float} or not isfinite(value)
                    for value in vector
                )
            ):
                return self._failed("invalid_embedding_response", started)
            ordered[index] = [float(value) for value in vector]
        if any(vector is None for vector in ordered):
            return self._failed("invalid_embedding_response", started)
        usage = decoded.get("usage")
        token_count = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        input_tokens = (
            token_count if type(token_count) is int and token_count >= 0 else None
        )
        return EmbeddingGenerationResult.generated(
            vectors=[vector for vector in ordered if vector is not None],
            provider=self.name,
            model=self.model,
            input_tokens=input_tokens,
            latency_ms=self._latency_ms(started),
        )

    def _failed(
        self, error_code: str, started: float, *, status: str = "ERROR"
    ) -> EmbeddingGenerationResult:
        return EmbeddingGenerationResult.failed(
            provider=self.name,
            model=self.model,
            error_code=error_code,
            status=status,
            latency_ms=self._latency_ms(started),
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return round((perf_counter() - started) * 1000)


def build_embedding_provider() -> EmbeddingProvider | None:
    from app.core.config import get_settings

    settings = get_settings()
    if (
        not settings.chatbot_ai_enabled
        or not settings.chatbot_ai_embeddings_enabled
        or settings.chatbot_ai_provider.casefold() != "openai"
        or settings.openai_api_key is None
    ):
        return None
    try:
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.chatbot_ai_embedding_model,
            dimensions=settings.chatbot_ai_embedding_dimensions,
        )
    except ValueError:
        return None
