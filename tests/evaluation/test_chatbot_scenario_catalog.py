import json
from pathlib import Path

from app.modules.chatbot.schemas.chatbot import ChatbotIntent


def test_intent_scenario_catalog_is_complete_and_uses_closed_ai_labels():
    path = Path(__file__).parent / "data" / "chatbot_intent_scenarios.json"
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    ids = [scenario["id"] for scenario in scenarios]
    ai_intents = {intent.value for intent in ChatbotIntent}

    assert len(scenarios) >= 10
    assert len(ids) == len(set(ids))
    for scenario in scenarios:
        assert scenario["message"].strip()
        assert scenario["expected_control_route"].strip()
        expected_ai_intent = scenario["expected_ai_intent"]
        if expected_ai_intent is not None:
            assert expected_ai_intent in ai_intents
            assert scenario["ai_call_allowed"] is True
        else:
            assert scenario["ai_call_allowed"] is False
