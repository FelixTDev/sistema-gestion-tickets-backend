"""Repair and enforce the ticket assignment-state invariant."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "0018_ticket_assignment_invariant"
down_revision = "0017_chatbot_ai_trace"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "ck_tickets_assignment_state"
_REPAIR_ACTION = "SYSTEM_ASSIGNMENT_REPAIR"
_MISSING_ADVISOR_REASON = (
    "Reparación defensiva: el estado requería asesor, pero el ticket no tenía "
    "un asesor asignado."
)
_NEW_STATE_REASON = (
    "Reparación defensiva: un ticket NUEVO no puede conservar asesor ni marcas "
    "temporales de asignación o cierre."
)
_STATUSES_REQUIRING_ADVISOR = (
    "ASIGNADO",
    "EN_PROCESO",
    "PENDIENTE_CLIENTE",
    "RESUELTO",
)
_CONSTRAINT_SQL = (
    "(status = 'NUEVO' AND assigned_advisor_id IS NULL) OR "
    "(status IN ('ASIGNADO', 'EN_PROCESO', 'PENDIENTE_CLIENTE', 'RESUELTO') "
    "AND assigned_advisor_id IS NOT NULL) OR "
    "status IN ('CERRADO', 'CANCELADO')"
)

_tickets = sa.table(
    "tickets",
    sa.column("id", sa.String(36)),
    sa.column("status", sa.String(30)),
    sa.column("assigned_advisor_id", sa.String(36)),
    sa.column("assigned_at", sa.DateTime(timezone=True)),
    sa.column("resolved_at", sa.DateTime(timezone=True)),
    sa.column("closed_at", sa.DateTime(timezone=True)),
    sa.column("cancelled_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("version", sa.Integer()),
)
_ticket_history = sa.table(
    "ticket_history",
    sa.column("id", sa.String(36)),
    sa.column("ticket_id", sa.String(36)),
    sa.column("actor_id", sa.String(36)),
    sa.column("action", sa.String(50)),
    sa.column("old_value", sa.Text()),
    sa.column("new_value", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_ticket_assignments = sa.table(
    "ticket_assignments",
    sa.column("ticket_id", sa.String(36)),
    sa.column("unassigned_at", sa.DateTime(timezone=True)),
)
_audit_logs = sa.table(
    "audit_logs",
    sa.column("id", sa.String(36)),
    sa.column("event_type", sa.String(80)),
    sa.column("action", sa.String(80)),
    sa.column("actor_user_id", sa.String(36)),
    sa.column("actor_role", sa.String(30)),
    sa.column("resource_type", sa.String(50)),
    sa.column("resource_id", sa.String(128)),
    sa.column("target_user_id", sa.String(36)),
    sa.column("occurred_at", sa.DateTime(timezone=True)),
    sa.column("success", sa.Boolean()),
    sa.column("error_code", sa.String(64)),
    sa.column("before_data", sa.JSON()),
    sa.column("after_data", sa.JSON()),
    sa.column("metadata_json", sa.JSON()),
    sa.column("request_id", sa.String(128)),
    sa.column("correlation_id", sa.String(128)),
    sa.column("ip_address", sa.String(45)),
    sa.column("user_agent", sa.Text()),
    sa.column("dedupe_key", sa.String(255)),
)


def _deterministic_id(kind: str, ticket_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"gnb-ticket-backend:0018:{kind}:{ticket_id}"))


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _repair_inconsistent_tickets(connection: Connection) -> int:
    inconsistent = (
        connection.execute(
            sa.select(
                _tickets.c.id,
                _tickets.c.status,
                _tickets.c.assigned_advisor_id,
                _tickets.c.assigned_at,
                _tickets.c.resolved_at,
                _tickets.c.closed_at,
                _tickets.c.cancelled_at,
            ).where(
                sa.or_(
                    sa.and_(
                        _tickets.c.status.in_(_STATUSES_REQUIRING_ADVISOR),
                        _tickets.c.assigned_advisor_id.is_(None),
                    ),
                    sa.and_(
                        _tickets.c.status == "NUEVO",
                        sa.or_(
                            _tickets.c.assigned_advisor_id.is_not(None),
                            _tickets.c.assigned_at.is_not(None),
                            _tickets.c.resolved_at.is_not(None),
                            _tickets.c.closed_at.is_not(None),
                            _tickets.c.cancelled_at.is_not(None),
                        ),
                    ),
                )
            )
        )
        .mappings()
        .all()
    )

    for ticket in inconsistent:
        ticket_id = str(ticket["id"])
        old_status = str(ticket["status"])
        repair_reason = (
            _NEW_STATE_REASON if old_status == "NUEVO" else _MISSING_ADVISOR_REASON
        )
        occurred_at = datetime.now(UTC).replace(tzinfo=None)
        history_id = _deterministic_id("history", ticket_id)
        audit_id = _deterministic_id("audit", ticket_id)
        dedupe_key = f"ticket_assignment_repair:0018:{ticket_id}"

        history_exists = connection.scalar(
            sa.select(sa.literal(1)).where(_ticket_history.c.id == history_id)
        )
        if history_exists is None:
            connection.execute(
                _ticket_history.insert().values(
                    id=history_id,
                    ticket_id=ticket_id,
                    actor_id=None,
                    action=_REPAIR_ACTION,
                    old_value=old_status,
                    new_value="NUEVO",
                    description=repair_reason,
                    created_at=occurred_at,
                )
            )

        audit_exists = connection.scalar(
            sa.select(sa.literal(1)).where(_audit_logs.c.dedupe_key == dedupe_key)
        )
        if audit_exists is None:
            before_data = {
                "status": old_status,
                "assigned_advisor_id": ticket["assigned_advisor_id"],
                "assigned_at": _json_value(ticket["assigned_at"]),
                "resolved_at": _json_value(ticket["resolved_at"]),
                "closed_at": _json_value(ticket["closed_at"]),
                "cancelled_at": _json_value(ticket["cancelled_at"]),
            }
            connection.execute(
                _audit_logs.insert().values(
                    id=audit_id,
                    event_type="TICKET",
                    action=_REPAIR_ACTION,
                    actor_user_id=None,
                    actor_role="SYSTEM",
                    resource_type="TICKET",
                    resource_id=ticket_id,
                    target_user_id=None,
                    occurred_at=occurred_at,
                    success=True,
                    error_code=None,
                    before_data=before_data,
                    after_data={
                        "status": "NUEVO",
                        "assigned_advisor_id": None,
                        "assigned_at": None,
                        "resolved_at": None,
                        "closed_at": None,
                        "cancelled_at": None,
                    },
                    metadata_json={"reason": repair_reason, "revision": revision},
                    request_id=None,
                    correlation_id=None,
                    ip_address=None,
                    user_agent=None,
                    dedupe_key=dedupe_key,
                )
            )

        connection.execute(
            _ticket_assignments.update()
            .where(_ticket_assignments.c.ticket_id == ticket_id)
            .where(_ticket_assignments.c.unassigned_at.is_(None))
            .values(unassigned_at=occurred_at)
        )
        connection.execute(
            _tickets.update()
            .where(_tickets.c.id == ticket_id)
            .values(
                status="NUEVO",
                assigned_advisor_id=None,
                assigned_at=None,
                resolved_at=None,
                closed_at=None,
                cancelled_at=None,
                updated_at=occurred_at,
                version=_tickets.c.version + 1,
            )
        )

    return len(inconsistent)


def upgrade() -> None:
    connection = op.get_bind()
    _repair_inconsistent_tickets(connection)

    constraint_names = {
        constraint["name"]
        for constraint in sa.inspect(connection).get_check_constraints("tickets")
    }
    if _CONSTRAINT_NAME not in constraint_names:
        op.create_check_constraint(_CONSTRAINT_NAME, "tickets", _CONSTRAINT_SQL)


def downgrade() -> None:
    constraint_names = {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints("tickets")
    }
    if _CONSTRAINT_NAME in constraint_names:
        op.drop_constraint(_CONSTRAINT_NAME, "tickets", type_="check")
