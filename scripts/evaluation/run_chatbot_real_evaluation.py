"""Run capped live chatbot checks against synthetic data and public FAQs only."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_PATH = Path(__file__).with_name("chatbot_real_scenarios.json")
LEDGER_PATH = ROOT / "docs" / "evaluation" / "chatbot_real_evaluation.json"
MODEL = "gpt-5.4-mini"
MAX_CALLS = 150
MAX_CHECKPOINT_CALLS = 40
MAX_RETEST_CALLS = 100
MAX_TOKENS = 500_000
MAX_ESTIMATED_COST_USD = 0.50
ABSOLUTE_ESTIMATED_COST_USD = 1.0
INPUT_USD_PER_MILLION = 0.75
OUTPUT_USD_PER_MILLION = 4.50
OFFICIAL_PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-5.4-mini"


class EvaluationLimitReached(Exception):
    """Raised before an API call if a configured hard limit would be crossed."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _provider_input_estimate(request: Any) -> int:
    serialized_input = {
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
    # UTF-8 bytes plus fixed framing reserve conservatively bound tokenizer use.
    return _json_bytes(serialized_input) + len(request.policy.encode("utf-8")) + 1024


def _estimated_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION)
        / 1_000_000,
        8,
    )


def _ticket_count_unchanged(
    ticket_count_at_flow_start: int,
    ticket_count_before_confirmation: int,
) -> bool:
    return ticket_count_before_confirmation == ticket_count_at_flow_start


def _scenarios_for_stage(
    stage: str, scenario_data: dict[str, Any]
) -> list[dict[str, Any]]:
    scenario_key = "checkpoint" if stage == "checkpoint" else "final"
    return scenario_data[scenario_key]


def _is_public_faq_context(request: Any) -> bool:
    for source in request.context:
        hostname = urlsplit(source.source_url or "").hostname
        if (
            source.status != "PUBLISHED"
            or not source.source_verified_at
            or hostname not in {"bancognb.com.pe", "www.bancognb.com.pe"}
        ):
            return False
    return True


class EvaluationLedger:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {
                "schema_version": 1,
                "model": MODEL,
                "created_at": _utc_now(),
                "pricing": {
                    "input_usd_per_million_tokens": INPUT_USD_PER_MILLION,
                    "output_usd_per_million_tokens": OUTPUT_USD_PER_MILLION,
                    "source": OFFICIAL_PRICING_URL,
                    "estimate_note": (
                        "Lista estándar; no descuenta tokens en caché ni aplica "
                        + "el incentivo gratuito de la cuenta."
                    ),
                },
                "limits": {
                    "provider_calls": MAX_CALLS,
                    "tokens": MAX_TOKENS,
                    "estimated_cost_usd": MAX_ESTIMATED_COST_USD,
                    "absolute_estimated_cost_usd": ABSOLUTE_ESTIMATED_COST_USD,
                    "output_tokens_per_call": 128,
                    "embeddings_enabled": False,
                },
                "runs": [],
                "totals": {},
            }
            self._write()
        if self.data.get("schema_version") != 1 or self.data.get("model") != MODEL:
            raise SystemExit(
                "El registro existente no coincide con el modelo permitido."
            )
        if not isinstance(self.data.get("runs"), list):
            raise SystemExit("El registro JSON no tiene una lista de corridas válida.")
        self._refresh_totals()

    def _calls(self) -> list[dict[str, Any]]:
        return [
            call
            for run in self.data["runs"]
            for call in run.get("provider_calls", [])
            if call.get("provider_call") is True
        ]

    def _refresh_totals(self) -> None:
        calls = self._calls()
        self.data["totals"] = {
            "provider_calls": len(calls),
            "actual_tokens": sum(
                int(call.get("tokens", {}).get("total") or 0) for call in calls
            ),
            "conservative_reserved_tokens": sum(
                int(call.get("budget_reservation", {}).get("tokens") or 0)
                for call in calls
            ),
            "actual_estimated_cost_usd": round(
                sum(
                    float(
                        call.get("estimated_cost_usd")
                        if call.get("estimated_cost_usd") is not None
                        else call.get("budget_reservation", {}).get("cost_usd", 0.0)
                    )
                    for call in calls
                ),
                8,
            ),
            "conservative_reserved_cost_usd": round(
                sum(
                    float(call.get("budget_reservation", {}).get("cost_usd") or 0.0)
                    for call in calls
                ),
                8,
            ),
        }

    def _write(self) -> None:
        self._refresh_totals()
        temp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    def existing_run(self, stage: str, scenario_id: str) -> dict[str, Any] | None:
        return next(
            (
                run
                for run in self.data["runs"]
                if run.get("stage") == stage and run.get("scenario_id") == scenario_id
            ),
            None,
        )

    def begin_run(self, stage: str, scenario: dict[str, Any]) -> dict[str, Any]:
        existing = self.existing_run(stage, scenario["id"])
        if existing is not None:
            if existing.get("status") == "COMPLETED":
                return existing
            raise SystemExit(
                "Hay una corrida incompleta con este ID; no se repetirá "
                "una posible llamada."
            )
        run = {
            "stage": stage,
            "scenario_id": scenario["id"],
            "kind": scenario.get("kind"),
            "topic": scenario.get("topic"),
            "question": scenario.get("question"),
            "started_at": _utc_now(),
            "status": "RUNNING",
            "provider_calls": [],
        }
        self.data["runs"].append(run)
        self._write()
        return run

    def reserve_call(
        self, stage: str, run: dict[str, Any], request: Any
    ) -> dict[str, Any]:
        calls = self._calls()
        stage_call_count = sum(1 for call in calls if call.get("stage") == stage)
        if len(calls) >= MAX_CALLS:
            raise EvaluationLimitReached(
                "Se alcanzaría el límite global de 150 llamadas."
            )
        if stage == "checkpoint" and stage_call_count >= MAX_CHECKPOINT_CALLS:
            raise EvaluationLimitReached(
                "Se alcanzaría el límite de 40 llamadas del punto intermedio."
            )
        if stage == "retest" and stage_call_count >= MAX_RETEST_CALLS:
            raise EvaluationLimitReached(
                "Se alcanzaría el límite de 100 llamadas de la repetición."
            )
        estimated_input = _provider_input_estimate(request)
        reserved_tokens = estimated_input + 128
        reserved_cost = _estimated_cost(estimated_input, 128)
        totals = self.data["totals"]
        if int(totals["conservative_reserved_tokens"]) + reserved_tokens > MAX_TOKENS:
            raise EvaluationLimitReached(
                "Se alcanzaría el límite global de 500 000 tokens."
            )
        next_cost = float(totals["conservative_reserved_cost_usd"]) + reserved_cost
        if (
            next_cost > MAX_ESTIMATED_COST_USD
            or next_cost > ABSOLUTE_ESTIMATED_COST_USD
        ):
            raise EvaluationLimitReached(
                "Se alcanzaría el límite global de costo estimado."
            )
        return {
            "input_tokens_upper_bound": estimated_input,
            "tokens": reserved_tokens,
            "cost_usd": reserved_cost,
            "pricing_source": OFFICIAL_PRICING_URL,
        }

    def start_provider_call(
        self,
        stage: str,
        run: dict[str, Any],
        request: Any,
        reservation: dict[str, Any],
    ) -> dict[str, Any]:
        call = {
            "stage": stage,
            "provider_call": True,
            "started_at": _utc_now(),
            "question": run.get("question"),
            "provider_input": request.user_message,
            "model": MODEL,
            "max_output_tokens": 128,
            "raw_output": None,
            "sources": [
                {
                    "id": source.source_id,
                    "title": source.title,
                    "category": source.category,
                    "url": source.source_url,
                    "verified_at": source.source_verified_at,
                }
                for source in request.context
            ],
            "response_source": None,
            "fallback_reason": None,
            "validation_subrule": None,
            "tokens": {"input": None, "output": None, "total": None},
            "estimated_cost_usd": None,
            "budget_reservation": reservation,
            "provider_status": "IN_PROGRESS",
        }
        run["provider_calls"].append(call)
        self._write()
        return call

    def complete_provider_call(self, call: dict[str, Any], result: Any) -> None:
        call.update(
            {
                "completed_at": _utc_now(),
                "provider_status": result.status,
                "provider_error_code": result.error_code,
                "raw_output": result.raw_output,
                "tokens": {
                    "input": result.input_tokens,
                    "output": result.output_tokens,
                    "total": result.total_tokens,
                },
            }
        )
        if result.input_tokens is not None and result.output_tokens is not None:
            call["estimated_cost_usd"] = _estimated_cost(
                result.input_tokens, result.output_tokens
            )
            call["cost_method"] = "actual_provider_usage_at_standard_list_rates"
        else:
            call["estimated_cost_usd"] = call["budget_reservation"]["cost_usd"]
            call["cost_method"] = "conservative_utf8_upper_bound"
        self._write()

    def complete_run(
        self,
        run: dict[str, Any],
        *,
        response: dict[str, Any],
        passed: bool | None,
        review_status: str,
    ) -> None:
        run.update(
            {
                "completed_at": _utc_now(),
                "status": "COMPLETED",
                "response": response,
                "passed": passed,
                "review_status": review_status,
            }
        )
        for call in run["provider_calls"]:
            call["response_source"] = response.get("response_source")
            call["fallback_reason"] = response.get("fallback_reason")
            call["validation_subrule"] = response.get("validation_subrule")
            call["sources"] = response.get("sources", call.get("sources", []))
        self._write()


class BudgetedRecordingProvider:
    name = "openai"

    def __init__(self, provider: Any, ledger: EvaluationLedger, stage: str) -> None:
        self.provider = provider
        self.ledger = ledger
        self.stage = stage
        self.model = provider.model
        self.current_run: dict[str, Any] | None = None
        self.abort_reason: str | None = None

    def generate(self, request: Any) -> Any:
        from app.modules.chatbot.services.ai_provider import AIGenerationResult

        run = self.current_run
        if run is None:
            self.abort_reason = "provider_call_without_scenario"
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code=self.abort_reason,
            )
        if not _is_public_faq_context(request):
            self.abort_reason = "evaluation_context_not_public_faq"
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code=self.abort_reason,
            )
        try:
            reservation = self.ledger.reserve_call(self.stage, run, request)
        except EvaluationLimitReached as error:
            self.abort_reason = str(error)
            return AIGenerationResult.failed(
                status="UNAVAILABLE",
                provider=self.name,
                model=self.model,
                error_code="evaluation_budget_limit",
            )
        call = self.ledger.start_provider_call(self.stage, run, request, reservation)
        result = self.provider.generate(request)
        self.ledger.complete_provider_call(call, result)
        if result.error_code in {"openai_http_401", "openai_http_429"}:
            self.abort_reason = result.error_code
        return result


def _configure_safe_sqlite(path: Path, password: str) -> None:
    os.environ.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{path.as_posix()}",
            "CHATBOT_AI_ENABLED": "true",
            "CHATBOT_AI_PROVIDER": "openai",
            "CHATBOT_AI_MODEL": MODEL,
            "CHATBOT_AI_EMBEDDINGS_ENABLED": "false",
            "CHATBOT_AI_MAX_OUTPUT_TOKENS": "128",
            "CHATBOT_AI_TOTAL_MAX_CALLS": str(MAX_CALLS),
            "CHATBOT_AI_TOTAL_MAX_TOKENS": str(MAX_TOKENS),
            "CHATBOT_AI_TOTAL_ESTIMATED_COST_USD": str(MAX_ESTIMATED_COST_USD),
            "CHATBOT_AI_DAILY_BUDGET_USD": str(MAX_ESTIMATED_COST_USD),
            "CHATBOT_AI_MAX_PROVIDER_CALLS_PER_ACTOR_PER_HOUR": "20",
            "CHATBOT_AI_MAX_RESPONSES_PER_CONVERSATION": "1",
            "DEMO_CLIENT_PASSWORD": password,
            "DEMO_ADVISOR_PASSWORD": password,
            "DEMO_SUPERVISOR_PASSWORD": password,
            "DEMO_SECOND_CLIENT_PASSWORD": password,
            "EMAIL_PROVIDER": "noop",
        }
    )


def _prepare_database(engine: Any, password: str) -> list[str]:
    from sqlmodel import Session, select

    from app.db.init_db import create_db_and_tables
    from app.modules.tickets.models.ticket import Ticket
    from app.modules.usuarios.models.role import Role
    from app.modules.usuarios.models.user import User
    from app.modules.usuarios.models.user_preference import UserPreference
    from app.seed.demo_data import hash_password, seed_demo_data

    if engine.dialect.name != "sqlite":
        raise SystemExit("Evaluación detenida: el motor configurado no es SQLite.")
    create_db_and_tables()
    emails = [f"evaluador-{index:02d}@example.com" for index in range(1, 6)]
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)
        client_role = session.exec(select(Role).where(Role.name == "CLIENTE")).one()
        for index, email in enumerate(emails, start=1):
            user = User(
                full_name=f"Evaluador sintético {index:02d}",
                email=email,
                password_hash=hash_password(password),
                role_id=client_role.id,
                email_verified=True,
            )
            session.add(user)
            session.flush()
            session.add(UserPreference(user_id=user.id))
        session.commit()
        if session.exec(select(Ticket.id)).first() is not None:
            raise SystemExit("Evaluación detenida: la SQLite no está vacía de tickets.")
    return emails


def _login(client: Any, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    if response.status_code != 200:
        raise SystemExit("No se pudo preparar el actor sintético para la evaluación.")
    return response.json()["access_token"]


def _send_question(
    client: Any, token: str, question: str
) -> tuple[dict[str, Any], str]:
    headers = {"Authorization": f"Bearer {token}"}
    conversation = client.post("/api/v1/chat/conversations", headers=headers)
    if conversation.status_code != 201:
        raise RuntimeError(
            f"No se pudo crear una conversación sintética: {conversation.status_code}"
        )
    conversation_id = conversation.json()["id"]
    response = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers=headers,
        json={"content": question},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"El turno conversacional respondió HTTP {response.status_code}."
        )
    return response.json(), conversation_id


def _message_trace(engine: Any, message_id: str) -> dict[str, Any]:
    from sqlmodel import Session

    from app.modules.chatbot.models.message import ChatMessage

    with Session(engine) as session:
        message = session.get(ChatMessage, message_id)
        trace = message.ai_trace_json if message is not None else None
        return trace if isinstance(trace, dict) else {}


def _run_question(
    *,
    stage: str,
    scenario: dict[str, Any],
    actor_index: int,
    tokens: list[str],
    client: Any,
    engine: Any,
    recorder: BudgetedRecordingProvider,
    ledger: EvaluationLedger,
) -> bool:
    run = ledger.begin_run(stage, scenario)
    if run.get("status") == "COMPLETED":
        return False
    recorder.current_run = run
    token = tokens[actor_index % len(tokens)]
    try:
        response, _ = _send_question(client, token, scenario["question"])
    except Exception as error:
        ledger.complete_run(
            run,
            response={"error_type": type(error).__name__, "error": str(error)},
            passed=False,
            review_status="REQUEST_ERROR",
        )
        return True
    bot_message = response.get("bot_message") or {}
    trace = _message_trace(engine, bot_message.get("id", ""))
    answer = bot_message.get("content", "")
    sources = response.get("sources") or []
    kind = scenario.get("kind")
    if kind == "LEGITIMATE":
        passed: bool | None = response.get("response_source") == "AI" and bool(sources)
        review_status = "AUTOMATED_AI_AND_SOURCE_CHECK"
    else:
        passed = None
        review_status = "MANUAL_SAFETY_REVIEW_REQUIRED"
    ledger.complete_run(
        run,
        response={
            "answer": answer,
            "response_source": response.get("response_source"),
            "fallback_reason": (
                response.get("fallback_reason") or trace.get("fallback_reason")
            ),
            "validation_subrule": trace.get("validation_subrule"),
            "intent_classification": response.get("intent_classification"),
            "sources": sources,
            "provider_calls": len(run["provider_calls"]),
            "provider_status": trace.get("provider_status"),
            "provider": trace.get("provider"),
            "model": trace.get("model"),
            "provider_called": trace.get("provider_called"),
            "cache_hit": trace.get("cache_hit"),
            "routing_trace": trace,
            "tokens": {
                "input": trace.get("input_tokens"),
                "output": trace.get("output_tokens"),
                "total": trace.get("total_tokens"),
            },
            "estimated_cost_usd": trace.get("estimated_cost_usd"),
        },
        passed=passed,
        review_status=review_status,
    )
    return recorder.abort_reason is not None


def _run_flows(
    *,
    client: Any,
    engine: Any,
    client_token: str,
    advisor_token: str,
    recorder: BudgetedRecordingProvider,
    ledger: EvaluationLedger,
) -> bool:
    from sqlmodel import Session, func, select

    from app.modules.tickets.models.ticket import Ticket

    flow_scenarios = {
        "FLOW-BANKING-SCOPE": "banking_operation_scope",
        "FLOW-TICKET-CONFIRMATION": "ticket_creation_confirmation",
        "FLOW-ADVISOR-QUEUE": "advisor_context_queue",
    }
    for flow_id, topic in flow_scenarios.items():
        scenario = {"id": flow_id, "kind": "FLOW", "topic": topic}
        run = ledger.begin_run("final", scenario)
        if run.get("status") == "COMPLETED":
            continue
        recorder.current_run = run
        headers = {"Authorization": f"Bearer {client_token}"}
        before_calls = len(ledger._calls())
        evidence: dict[str, Any] = {"steps": []}
        with Session(engine) as session:
            ticket_count_at_flow_start = session.exec(
                select(func.count(Ticket.id))
            ).one()
        evidence["ticket_count_at_flow_start"] = ticket_count_at_flow_start
        passed = False
        try:
            if flow_id == "FLOW-BANKING-SCOPE":
                body, _ = _send_question(
                    client, client_token, "¿Puedes mostrarme mi saldo?"
                )
                answer = body["bot_message"]["content"]
                evidence["steps"].append(
                    {
                        "question": "¿Puedes mostrarme mi saldo?",
                        "answer": answer,
                        "response_source": body.get("response_source"),
                    }
                )
                passed = (
                    len(ledger._calls()) == before_calls
                    and body.get("response_source") == "DETERMINISTIC"
                    and "no" in answer.casefold()
                )
            elif flow_id == "FLOW-TICKET-CONFIRMATION":
                conversation = client.post(
                    "/api/v1/chat/conversations", headers=headers
                ).json()
                endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
                prompts = [
                    "Quiero crear un ticket",
                    "Categoría: BANCA_DIGITAL",
                    "Asunto: Acceso al portal de demostración",
                    "Descripción: No puedo ingresar al portal de pruebas.",
                    "Prioridad: MEDIA",
                ]
                replies = []
                for question in prompts:
                    reply = client.post(
                        endpoint, headers=headers, json={"content": question}
                    )
                    if reply.status_code != 200:
                        raise RuntimeError("Falló la captura de un campo del borrador.")
                    replies.append(reply.json())
                with Session(engine) as session:
                    ticket_count_before_confirmation = session.exec(
                        select(func.count(Ticket.id))
                    ).one()
                confirmation = client.post(
                    endpoint, headers=headers, json={"content": "Confirmo"}
                ).json()
                draft = confirmation["ticket_draft"]
                conversion = client.post(
                    f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
                    headers=headers,
                    json=draft["fields"],
                )
                evidence.update(
                    {
                        "ticket_count_before_confirmation": (
                            ticket_count_before_confirmation
                        ),
                        "confirmation_status": draft.get("status"),
                        "conversion_status_code": conversion.status_code,
                        "converted_ticket": (
                            conversion.json() if conversion.status_code == 201 else None
                        ),
                        "provider_calls_during_flow": len(ledger._calls())
                        - before_calls,
                    }
                )
                ticket = evidence["converted_ticket"] or {}
                passed = (
                    _ticket_count_unchanged(
                        ticket_count_at_flow_start,
                        ticket_count_before_confirmation,
                    )
                    and draft.get("status") == "CONFIRMED"
                    and conversion.status_code == 201
                    and ticket.get("source") == "CHATBOT"
                    and ticket.get("status") == "NUEVO"
                    and len(ledger._calls()) == before_calls
                )
            else:
                conversation = client.post(
                    "/api/v1/chat/conversations", headers=headers
                ).json()
                endpoint = f"/api/v1/chat/conversations/{conversation['id']}/messages"
                greeting = client.post(
                    endpoint, headers=headers, json={"content": "Hola"}
                ).json()
                handoff = client.post(
                    endpoint,
                    headers=headers,
                    json={"content": "Quiero hablar con un asesor"},
                ).json()
                prompts = [
                    "Categoría: SOLICITUDES_INFORMACION",
                    "Asunto: Orientación para mi caso de demostración",
                    "Descripción: Necesito que revisen este caso sintético.",
                    "Prioridad: MEDIA",
                ]
                replies = []
                for question in prompts:
                    reply = client.post(
                        endpoint, headers=headers, json={"content": question}
                    )
                    if reply.status_code != 200:
                        raise RuntimeError("Falló la captura del borrador de asesor.")
                    replies.append(reply.json())
                with Session(engine) as session:
                    ticket_count_before_confirmation = session.exec(
                        select(func.count(Ticket.id))
                    ).one()
                confirmation = client.post(
                    endpoint, headers=headers, json={"content": "Confirmo"}
                ).json()
                draft = confirmation["ticket_draft"]
                conversion = client.post(
                    f"/api/v1/chat/conversations/{conversation['id']}/convert-to-ticket",
                    headers=headers,
                    json=draft["fields"],
                )
                advisor_headers = {"Authorization": f"Bearer {advisor_token}"}
                queue = client.get(
                    "/api/v1/tickets/operations",
                    params={"queue": "unassigned", "page": 1, "page_size": 20},
                    headers=advisor_headers,
                )
                ticket = conversion.json() if conversion.status_code == 201 else {}
                queued = next(
                    (
                        item
                        for item in queue.json().get("items", [])
                        if item.get("id") == ticket.get("id")
                    ),
                    None,
                )
                evidence.update(
                    {
                        "greeting_response_source": greeting.get("response_source"),
                        "handoff_notice": handoff.get("bot_message", {}).get("content"),
                        "ticket_count_before_confirmation": (
                            ticket_count_before_confirmation
                        ),
                        "conversion_status_code": conversion.status_code,
                        "queue_status_code": queue.status_code,
                        "ticket_description": ticket.get("description"),
                        "advisor_queue_description": (
                            queued.get("description") if queued else None
                        ),
                        "provider_calls_during_flow": len(ledger._calls())
                        - before_calls,
                    }
                )
                summary = ticket.get("description", "")
                passed = (
                    _ticket_count_unchanged(
                        ticket_count_at_flow_start,
                        ticket_count_before_confirmation,
                    )
                    and conversion.status_code == 201
                    and queue.status_code == 200
                    and queued is not None
                    and "Resumen de conversación" in summary
                    and "Hola" in summary
                    and queued.get("description") == summary
                    and len(ledger._calls()) == before_calls
                )
            ledger.complete_run(
                run,
                response=evidence,
                passed=passed,
                review_status="AUTOMATED_DETERMINISTIC_FLOW_CHECK",
            )
        except Exception as error:
            ledger.complete_run(
                run,
                response={
                    **evidence,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                passed=False,
                review_status="FLOW_ERROR",
            )
            return True
    return recorder.abort_reason is not None


def run(stage: str, ledger_path: Path) -> None:
    scenario_data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    ledger = EvaluationLedger(ledger_path)
    password = f"synthetic-eval-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="chatbot-real-eval-") as temp_dir:
        db_path = Path(temp_dir) / "evaluation.sqlite3"
        _configure_safe_sqlite(db_path, password)
        from app.core.config import get_settings

        get_settings.cache_clear()
        settings = get_settings()
        if (
            not settings.chatbot_ai_enabled
            or settings.chatbot_ai_provider != "openai"
            or settings.chatbot_ai_model != MODEL
            or settings.chatbot_ai_embeddings_enabled
            or settings.chatbot_ai_max_output_tokens > 128
            or settings.chatbot_ai_total_max_calls > MAX_CALLS
            or settings.chatbot_ai_total_max_tokens > MAX_TOKENS
            or settings.chatbot_ai_total_estimated_cost_usd > MAX_ESTIMATED_COST_USD
            or settings.openai_api_key is None
        ):
            raise SystemExit(
                "Evaluación detenida: configuración no permitida o clave ausente."
            )
        from app.db.session import engine

        if engine.dialect.name != "sqlite":
            raise SystemExit("Evaluación detenida: solo se admite SQLite temporal.")
        actor_emails = _prepare_database(engine, password)
        from fastapi.testclient import TestClient

        from app.main import app
        from app.modules.chatbot.api.router import get_chatbot_service
        from app.modules.chatbot.services.chatbot_service import ChatbotService
        from app.modules.chatbot.services.openai_provider import OpenAIResponsesProvider

        base_provider = OpenAIResponsesProvider(
            api_key=settings.openai_api_key.get_secret_value(), model=MODEL
        )
        recorder = BudgetedRecordingProvider(base_provider, ledger, stage)
        service = ChatbotService(ai_provider=recorder)
        app.dependency_overrides[get_chatbot_service] = lambda: service
        try:
            with TestClient(app) as client:
                tokens = [_login(client, email, password) for email in actor_emails]
                advisor_token = _login(client, "asesor@demo.com", password)
                scenarios = _scenarios_for_stage(stage, scenario_data)
                scenario_sets = [(item, index) for index, item in enumerate(scenarios)]
                for scenario, index in scenario_sets:
                    should_stop = _run_question(
                        stage=stage,
                        scenario=scenario,
                        actor_index=index,
                        tokens=tokens,
                        client=client,
                        engine=engine,
                        recorder=recorder,
                        ledger=ledger,
                    )
                    if should_stop:
                        break
                if stage == "final" and recorder.abort_reason is None:
                    _run_flows(
                        client=client,
                        engine=engine,
                        client_token=tokens[0],
                        advisor_token=advisor_token,
                        recorder=recorder,
                        ledger=ledger,
                    )
        finally:
            app.dependency_overrides.clear()
            engine.dispose()
    ledger.data["completed_at"] = _utc_now()
    ledger.data["last_stage"] = stage
    ledger.data["abort_reason"] = recorder.abort_reason
    ledger._write()
    print(
        json.dumps(
            {
                "stage": stage,
                "abort_reason": recorder.abort_reason,
                "ledger": str(ledger.path),
                "totals": ledger.data["totals"],
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", choices=("checkpoint", "final", "retest"), required=True
    )
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    args = parser.parse_args()
    run(args.stage, args.ledger)


if __name__ == "__main__":
    main()
