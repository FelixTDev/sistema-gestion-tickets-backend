import json
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
)

_RESPONSES_URL = "https://api.openai.com/v1/responses"
_MAX_RESPONSE_BYTES = 1_000_000
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "source_ids"],
    "additionalProperties": False,
}


class OpenAIResponsesProvider:
    """Call the official Responses API without retaining provider-side state."""

    name = "openai"

    def __init__(self, *, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("La clave de OpenAI es obligatoria")
        if not model.strip():
            raise ValueError("El modelo de OpenAI es obligatorio")
        self.api_key = api_key.strip()
        self.model = model.strip()

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        started = perf_counter()
        payload = self._request_payload(request)
        http_request = Request(
            _RESPONSES_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=request.timeout_seconds) as response:
                body = response.read(_MAX_RESPONSE_BYTES)
        except HTTPError as error:
            return self._failed(
                status="ERROR",
                error_code=f"openai_http_{error.code}",
                started=started,
            )
        except TimeoutError:
            return self._failed(
                status="TIMEOUT",
                error_code="provider_timeout",
                started=started,
            )
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                return self._failed(
                    status="TIMEOUT",
                    error_code="provider_timeout",
                    started=started,
                )
            return self._failed(
                status="ERROR",
                error_code="provider_error",
                started=started,
            )
        except OSError:
            return self._failed(
                status="ERROR",
                error_code="provider_error",
                started=started,
            )

        try:
            decoded = json.loads(body.decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("status") != "completed":
                return self._failed(
                    status="ERROR",
                    error_code="invalid_provider_response",
                    started=started,
                )
            output_text = self._extract_output_text(decoded)
            if output_text is None:
                return self._failed(
                    status="ERROR",
                    error_code="invalid_provider_response",
                    started=started,
                )
            structured_output = json.loads(output_text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._failed(
                status="ERROR",
                error_code="invalid_provider_response",
                started=started,
            )

        if not isinstance(structured_output, dict):
            return self._failed(
                status="ERROR",
                error_code="invalid_provider_response",
                started=started,
            )
        text = structured_output.get("text")
        source_ids = structured_output.get("source_ids")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > request.max_output_chars
            or not isinstance(source_ids, list)
            or (request.grounding_required and not source_ids)
            or any(not isinstance(source_id, str) for source_id in source_ids)
        ):
            return self._failed(
                status="ERROR",
                error_code="invalid_provider_response",
                started=started,
            )

        return AIGenerationResult.generated(
            text=text.strip(),
            source_ids=source_ids,
            provider=self.name,
            model=self.model,
            latency_ms=self._latency_ms(started),
            input_tokens=self._usage_count(decoded, "input_tokens"),
            output_tokens=self._usage_count(decoded, "output_tokens"),
            total_tokens=self._usage_count(decoded, "total_tokens"),
        )

    def _request_payload(self, request: AIProviderRequest) -> dict[str, object]:
        input_payload = {
            "user_message": request.user_message,
            "grounding_required": request.grounding_required,
            "sources": [
                {
                    "id": source.source_id,
                    "title": source.title,
                    "category": source.category,
                    "content": source.content,
                }
                for source in request.context
            ],
        }
        return {
            "model": self.model,
            "store": False,
            "instructions": request.policy,
            "input": json.dumps(input_payload, ensure_ascii=False),
            "max_output_tokens": request.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grounded_banking_answer",
                    "strict": True,
                    "schema": _OUTPUT_SCHEMA,
                }
            },
        }

    @staticmethod
    def _extract_output_text(response: dict[str, object]) -> str | None:
        output = response.get("output")
        if not isinstance(output, list):
            return None
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if (
                    isinstance(content_item, dict)
                    and content_item.get("type") == "output_text"
                    and isinstance(content_item.get("text"), str)
                ):
                    text_parts.append(content_item["text"])
        return "".join(text_parts) if text_parts else None

    def _failed(
        self,
        *,
        status: str,
        error_code: str,
        started: float,
    ) -> AIGenerationResult:
        return AIGenerationResult.failed(
            status=status,
            provider=self.name,
            model=self.model,
            error_code=error_code,
            latency_ms=self._latency_ms(started),
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return round((perf_counter() - started) * 1000)

    @staticmethod
    def _usage_count(response: dict[str, object], name: str) -> int | None:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        count = usage.get(name)
        return count if type(count) is int and count >= 0 else None
