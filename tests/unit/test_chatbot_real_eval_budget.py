import json
from types import SimpleNamespace

import pytest

from scripts.evaluation.run_chatbot_real_evaluation import (
    EvaluationLedger,
    EvaluationLimitReached,
    _is_public_faq_context,
    _scenarios_for_stage,
    _ticket_count_unchanged,
)


def _request() -> SimpleNamespace:
    source = SimpleNamespace(
        source_id="faq-public-1",
        title="Orientación pública",
        category="INFORMACION_GENERAL",
        content="Contenido de prueba sin datos de cliente.",
        source_url="https://www.bancognb.com.pe/orientacion",
        source_verified_at="2026-01-01T00:00:00Z",
        status="PUBLISHED",
    )
    return SimpleNamespace(
        user_message="¿Dónde consulto la orientación pública?",
        grounding_required=True,
        context=(source,),
        policy="Usa únicamente la fuente pública verificada.",
    )


def test_evaluation_ledger_reserves_calls_before_the_checkpoint_limit(tmp_path) -> None:
    ledger = EvaluationLedger(tmp_path / "evaluation.json")
    run = ledger.begin_run(
        "checkpoint", {"id": "CP-TEST", "question": "consulta sintética"}
    )

    for _ in range(40):
        reservation = ledger.reserve_call("checkpoint", run, _request())
        call = ledger.start_provider_call("checkpoint", run, _request(), reservation)
        assert call["provider_status"] == "IN_PROGRESS"
        assert call["max_output_tokens"] == 128

    with pytest.raises(EvaluationLimitReached, match="40 llamadas"):
        ledger.reserve_call("checkpoint", run, _request())

    saved = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    assert saved["totals"]["provider_calls"] == 40
    assert saved["totals"]["conservative_reserved_tokens"] < 500_000


def test_evaluation_ledger_caps_retest_at_100_provider_calls(tmp_path) -> None:
    ledger = EvaluationLedger(tmp_path / "evaluation.json")
    ledger.data["runs"] = [
        {
            "stage": "retest",
            "scenario_id": "RETEST-CORPUS",
            "provider_calls": [
                {"stage": "retest", "provider_call": True} for _ in range(100)
            ],
        }
    ]
    ledger._write()
    run = ledger.begin_run(
        "retest", {"id": "RETEST-101", "question": "consulta sintética"}
    )

    with pytest.raises(EvaluationLimitReached, match="100 llamadas"):
        ledger.reserve_call("retest", run, _request())


def test_retest_uses_the_same_final_scenarios() -> None:
    final_scenarios = [{"id": "L-TEST"}, {"id": "A-TEST"}]
    scenario_data = {"checkpoint": [{"id": "CP-TEST"}], "final": final_scenarios}

    assert _scenarios_for_stage("retest", scenario_data) is final_scenarios


def test_evaluation_preflight_accepts_only_verified_public_faq_context() -> None:
    request = _request()
    assert _is_public_faq_context(request)

    request.context[0].source_url = "https://example.invalid/private"
    assert not _is_public_faq_context(request)


def test_flow_ticket_baseline_allows_preexisting_synthetic_tickets() -> None:
    assert _ticket_count_unchanged(0, 0)
    assert _ticket_count_unchanged(1, 1)
    assert not _ticket_count_unchanged(1, 2)


def test_existing_ledger_cannot_change_the_allowed_model(tmp_path) -> None:
    ledger_path = tmp_path / "evaluation.json"
    ledger_path.write_text(
        json.dumps({"schema_version": 1, "model": "other-model", "runs": []}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="modelo permitido"):
        EvaluationLedger(ledger_path)
