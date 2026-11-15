from app.core.config import Settings


def test_test_settings_disable_ai_and_do_not_load_an_api_key() -> None:
    settings = Settings(app_env="test")

    assert Settings.model_config.get("env_file") is None
    assert settings.app_env == "test"
    assert settings.chatbot_ai_enabled is False
    assert settings.chatbot_ai_provider == "noop"
    assert settings.chatbot_ai_embeddings_enabled is False
    assert settings.openai_api_key is None
    assert settings.chatbot_ai_api_key is None
