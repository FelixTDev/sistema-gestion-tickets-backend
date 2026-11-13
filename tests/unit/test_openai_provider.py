import json
from dataclasses import replace
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.core.config import Settings
from app.modules.chatbot.services import ai_provider, openai_provider
from app.modules.chatbot.services.ai_provider import (
    AIProviderRequest,
    KnowledgeContext,
    NoopAIProvider,
)
from app.modules.chatbot.services.openai_provider import OpenAIResponsesProvider


def provider_request() -> AIProviderRequest:
    return AIProviderRequest(
        user_message="¿Cómo bloqueo mi tarjeta?",
        context=(
            KnowledgeContext(
                source_id="faq-card-block",
                version=1,
                title="Bloqueo de tarjeta",
                category="TARJETAS",
                content="Comunícate con el canal oficial para bloquearla.",
                score=0.91,
                status="PUBLISHED",
                valid_from=None,
                valid_to=None,
            ),
        ),
        policy="Responde únicamente con el contexto autorizado.",
        prompt_version="v1",
        max_output_chars=1200,
        max_output_tokens=384,
        timeout_seconds=12,
    )


def response_body(
    payload: object,
    *,
    status: str = "completed",
    usage: object | None = None,
) -> bytes:
    response: dict[str, object] = {
        "status": status,
        "output": [
            {"type": "reasoning", "id": "rs_test"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(payload),
                    }
                ],
            },
        ],
    }
    if usage is not None:
        response["usage"] = usage
    return json.dumps(response).encode()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def test_responses_provider_sends_stateless_grounded_structured_request(monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout: float):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse(
            response_body(
                {
                    "text": "Comunícate con el canal oficial para bloquearla.",
                    "source_ids": ["faq-card-block"],
                },
                usage={"input_tokens": 81, "output_tokens": 24, "total_tokens": 105},
            )
        )

    monkeypatch.setattr(openai_provider, "urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(
        api_key="openai-secret-test",
        model="gpt-5.4-mini",
    )

    result = provider.generate(provider_request())

    assert result.status == "GENERATED"
    assert result.text == "Comunícate con el canal oficial para bloquearla."
    assert result.source_ids == ("faq-card-block",)
    assert result.provider == "openai"
    assert result.model == "gpt-5.4-mini"
    assert result.input_tokens == 81
    assert result.output_tokens == 24
    assert result.total_tokens == 105
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["timeout"] == 12
    assert captured["headers"]["Authorization"] == "Bearer openai-secret-test"
    payload = captured["payload"]
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 384
    assert payload["instructions"] == "Responde únicamente con el contexto autorizado."
    sent_input = json.loads(payload["input"])
    assert sent_input["user_message"] == "¿Cómo bloqueo mi tarjeta?"
    assert sent_input["sources"][0]["id"] == "faq-card-block"
    assert sent_input["sources"][0]["content"] == (
        "Comunícate con el canal oficial para bloquearla."
    )
    assert "openai-secret-test" not in json.dumps(payload)


def test_responses_provider_accepts_source_free_conversational_output(monkeypatch):
    monkeypatch.setattr(
        openai_provider,
        "urlopen",
        lambda _request, *, timeout: FakeResponse(
            response_body(
                {
                    "text": "Entiendo. ¿Qué parte de la respuesta no te ayudó?",
                    "source_ids": [],
                }
            )
        ),
    )
    provider = OpenAIResponsesProvider(
        api_key="openai-secret-test",
        model="gpt-5.4-mini",
    )
    request = replace(
        provider_request(),
        context=(),
        grounding_required=False,
    )

    result = provider.generate(request)

    assert result.status == "GENERATED"
    assert result.source_ids == ()


@pytest.mark.parametrize(
    ("http_status", "error_code"),
    [(401, "openai_http_401"), (429, "openai_http_429"), (500, "openai_http_500")],
)
def test_responses_provider_sanitizes_http_errors(
    monkeypatch,
    http_status: int,
    error_code: str,
):
    def fake_urlopen(_request, *, timeout: float):
        raise HTTPError(
            "https://api.openai.com/v1/responses",
            http_status,
            "provider error body contains sensitive details",
            {},
            BytesIO(b"private provider error body"),
        )

    monkeypatch.setattr(openai_provider, "urlopen", fake_urlopen)
    provider = OpenAIResponsesProvider(api_key="secret", model="gpt-5.4-mini")

    result = provider.generate(provider_request())

    assert result.status == "ERROR"
    assert result.error_code == error_code
    assert result.text is None
    assert "private provider error body" not in repr(result)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        response_body({"text": "answer", "source_ids": []}, status="incomplete"),
        response_body({"text": "", "source_ids": ["faq-card-block"]}),
        response_body({"text": "answer", "source_ids": "faq-card-block"}),
    ],
)
def test_responses_provider_falls_back_on_unusable_model_output(
    monkeypatch, body: bytes
):
    monkeypatch.setattr(
        openai_provider,
        "urlopen",
        lambda _request, *, timeout: FakeResponse(body),
    )
    provider = OpenAIResponsesProvider(api_key="secret", model="gpt-5.4-mini")

    result = provider.generate(provider_request())

    assert result.status == "ERROR"
    assert result.text is None
    assert result.source_ids == ()


def test_responses_provider_maps_timeout_without_leaking_request_data(monkeypatch):
    monkeypatch.setattr(
        openai_provider,
        "urlopen",
        lambda _request, *, timeout: (_ for _ in ()).throw(TimeoutError()),
    )
    provider = OpenAIResponsesProvider(api_key="secret", model="gpt-5.4-mini")

    result = provider.generate(provider_request())

    assert result.status == "TIMEOUT"
    assert result.error_code == "provider_timeout"


def test_openai_provider_requires_nonempty_secret_and_model():
    with pytest.raises(ValueError, match="clave"):
        OpenAIResponsesProvider(api_key="  ", model="gpt-5.4-mini")
    with pytest.raises(ValueError, match="modelo"):
        OpenAIResponsesProvider(api_key="secret", model=" ")


def test_actor_hourly_provider_limit_must_be_positive():
    with pytest.raises(ValueError, match="límite horario de llamadas IA"):
        Settings(
            app_env="test",
            chatbot_ai_max_provider_calls_per_actor_per_hour=0,
        )


def test_provider_factory_keeps_openai_disabled_without_explicit_enable(monkeypatch):
    settings = Settings(
        app_env="test",
        chatbot_ai_enabled=False,
        chatbot_ai_provider="openai",
        chatbot_ai_model="gpt-5.4-mini",
        openai_api_key="api-secret-value",
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    provider = ai_provider.build_ai_provider()

    assert isinstance(provider, NoopAIProvider)
    assert "api-secret-value" not in repr(settings)


def test_provider_factory_selects_openai_only_with_explicit_enable_and_key(
    monkeypatch,
):
    settings = Settings(
        app_env="test",
        chatbot_ai_enabled=True,
        chatbot_ai_provider="openai",
        chatbot_ai_model="gpt-5.4-mini",
        openai_api_key="api-secret-value",
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    provider = ai_provider.build_ai_provider()

    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.model == "gpt-5.4-mini"
    assert "api-secret-value" not in repr(settings)


def test_provider_factory_falls_back_to_noop_when_openai_key_is_missing(monkeypatch):
    settings = Settings(
        app_env="test",
        chatbot_ai_enabled=True,
        chatbot_ai_provider="openai",
        chatbot_ai_model="gpt-5.4-mini",
        openai_api_key=None,
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    provider = ai_provider.build_ai_provider()

    assert isinstance(provider, NoopAIProvider)
    assert provider.reason == "provider_not_configured"
