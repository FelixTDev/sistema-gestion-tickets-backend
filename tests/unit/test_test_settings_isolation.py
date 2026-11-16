from app.core.config import Settings


def test_test_settings_disable_ai_and_do_not_load_an_api_key() -> None:
    settings = Settings(app_env="test")

    assert Settings.model_config.get("env_file") is None
    assert settings.app_env == "test"
    assert settings.chatbot_ai_enabled is False
    assert settings.chatbot_ai_provider == "noop"
    assert settings.chatbot_ai_embeddings_enabled is False
    assert settings.chatbot_contextual_followups_enabled is True
    assert settings.chatbot_quick_replies_enabled is True
    assert settings.chatbot_escalation_ticket_enabled is True
    assert settings.chatbot_proactive_notices_enabled is True
    assert settings.chatbot_aggregate_metrics_enabled is True
    assert settings.chatbot_persona_enabled is True
    assert settings.openai_api_key is None
    assert settings.chatbot_ai_api_key is None


def test_safe_demo_ai_defaults_disable_embeddings_and_allow_complete_output(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CHATBOT_AI_EMBEDDINGS_ENABLED", raising=False)
    monkeypatch.delenv("CHATBOT_AI_MAX_OUTPUT_TOKENS", raising=False)
    settings = Settings(app_env="test")

    assert settings.chatbot_ai_enabled is False
    assert settings.chatbot_ai_model == "gpt-5.4-mini"
    assert settings.chatbot_ai_embeddings_enabled is False
    assert settings.chatbot_ai_max_output_tokens == 384


def test_evaluation_budgets_keep_global_caps_and_allow_custom_output_tokens() -> None:
    settings = Settings(
        app_env="test",
        chatbot_ai_max_output_tokens=512,
        chatbot_ai_total_max_calls=1000,
        chatbot_ai_total_max_tokens=1_000_000,
        chatbot_ai_total_estimated_cost_usd=5.0,
    )

    assert settings.chatbot_ai_max_output_tokens == 512
    assert settings.chatbot_ai_total_max_calls == 150
    assert settings.chatbot_ai_total_max_tokens == 500_000
    assert settings.chatbot_ai_total_estimated_cost_usd == 0.50
