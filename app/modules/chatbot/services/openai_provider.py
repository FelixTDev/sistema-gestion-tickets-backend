import json
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.modules.chatbot.schemas.chatbot import ChatbotIntent
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
        "intent_classification": {
            "type": "string",
            "enum": [intent.value for intent in ChatbotIntent],
        },
    },
    "required": ["text", "source_ids", "intent_classification"],
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
            if not isinstance(decoded, dict):
                return self._failed(
                    status="ERROR",
                    error_code="invalid_provider_response",
                    started=started,
                )
            response_status = decoded.get("status")
            if response_status == "incomplete":
                incomplete_details = decoded.get("incomplete_details")
                incomplete_reason = (
                    incomplete_details.get("reason")
                    if isinstance(incomplete_details, dict)
                    else None
                )
                safe_reason = (
                    incomplete_reason
                    if incomplete_reason in {"max_output_tokens", "content_filter"}
                    else "provider"
                )
                return self._failed(
                    status="ERROR",
                    error_code=f"incomplete_{safe_reason}",
                    started=started,
                )
            if response_status != "completed":
                return self._failed(
                    status="ERROR",
                    error_code="invalid_provider_response",
                    started=started,
                )
            if self._contains_refusal(decoded):
                return self._failed(
                    status="REFUSED",
                    error_code="provider_refusal",
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
        intent_classification = structured_output.get("intent_classification")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > request.max_output_chars
            or not isinstance(source_ids, list)
            or (request.grounding_required and not source_ids)
            or any(not isinstance(source_id, str) for source_id in source_ids)
            or not isinstance(intent_classification, str)
            or intent_classification not in {intent.value for intent in ChatbotIntent}
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
            intent_classification=intent_classification,
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
        payload: dict[str, object] = {
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
        if self.model == "gpt-5.4-mini":
            payload["reasoning"] = {"effort": "none"}
        return payload

    @staticmethod
    def _contains_refusal(response: dict[str, object]) -> bool:
        output = response.get("output")
        if not isinstance(output, list):
            return False
        return any(
            isinstance(item, dict)
            and isinstance(item.get("content"), list)
            and any(
                isinstance(content_item, dict) and content_item.get("type") == "refusal"
                for content_item in item["content"]
            )
            for item in output
        )

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
