"""Networkless ASGI target for the browser E2E with a fake FAQ provider."""

from urllib.parse import urlsplit

from app.core.config import get_settings
from app.main import app
from app.modules.chatbot.api.router import get_chatbot_service
from app.modules.chatbot.services import chatbot_service as chatbot_module
from app.modules.chatbot.services import rag_service as rag_module
from app.modules.chatbot.services.ai_provider import (
    AIGenerationResult,
    AIProviderRequest,
)
from app.modules.chatbot.services.chatbot_service import ChatbotService


class FakePublicFAQProvider:
    name = "fake-e2e"
    model = "fake-public-faq"

    def generate(self, request: AIProviderRequest) -> AIGenerationResult:
        if not request.grounding_required or not request.context:
            return AIGenerationResult.failed(
                status="ERROR",
                provider=self.name,
                model=self.model,
                error_code="fake_provider_requires_public_faq_context",
            )

        source = request.context[0]
        hostname = urlsplit(source.source_url or "").hostname
        if (
            source.status != "PUBLISHED"
            or not source.source_verified_at
            or hostname not in {"bancognb.com.pe", "www.bancognb.com.pe"}
        ):
            return AIGenerationResult.failed(
                status="ERROR",
                provider=self.name,
                model=self.model,
                error_code="fake_provider_rejected_unverified_context",
            )

        return AIGenerationResult.generated(
            text=source.content[: request.max_output_chars],
            source_ids=[source.source_id],
            provider=self.name,
            model=self.model,
            input_tokens=24,
            output_tokens=24,
            total_tokens=48,
            intent_classification="FAQ_QUERY",
        )


settings = get_settings().model_copy(
    update={
        "chatbot_ai_enabled": True,
        "chatbot_ai_provider": "fake-e2e",
        "chatbot_ai_model": "fake-public-faq",
        "chatbot_ai_min_confidence": 0.70,
        "chatbot_ai_embeddings_enabled": False,
    }
)
chatbot_module.get_settings = lambda: settings
rag_module.get_settings = lambda: settings
app.dependency_overrides[get_chatbot_service] = lambda: ChatbotService(
    ai_provider=FakePublicFAQProvider()
)
