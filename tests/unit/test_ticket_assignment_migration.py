import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa


def _load_migration():
    migration_path = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "0018_ticket_assignment_invariant.py"
    )
    spec = importlib.util.spec_from_file_location(
        "ticket_assignment_invariant_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_schema() -> tuple[sa.MetaData, sa.Table, sa.Table, sa.Table, sa.Table]:
    metadata = sa.MetaData()
    tickets = sa.Table(
        "tickets",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("assigned_advisor_id", sa.String(36)),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
    )
    history = sa.Table(
        "ticket_history",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36)),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    audit = sa.Table(
        "audit_logs",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("actor_user_id", sa.String(36)),
        sa.Column("actor_role", sa.String(30)),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("target_user_id", sa.String(36)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("before_data", sa.JSON),
        sa.Column("after_data", sa.JSON),
        sa.Column("metadata_json", sa.JSON),
        sa.Column("request_id", sa.String(128)),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.Text),
        sa.Column("dedupe_key", sa.String(255), unique=True),
    )
    assignments = sa.Table(
        "ticket_assignments",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.String(36), nullable=False),
        sa.Column("advisor_id", sa.String(36), nullable=False),
        sa.Column("assigned_by", sa.String(36), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True)),
    )
    return metadata, tickets, history, audit, assignments


def test_repair_is_defensive_audited_and_idempotent():
    migration = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata, tickets, history, audit, assignments = _migration_schema()
    metadata.create_all(engine)
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    invalid_statuses = ("ASIGNADO", "EN_PROCESO", "PENDIENTE_CLIENTE", "RESUELTO")

    rows = [
        {
            "id": f"invalid-{index}",
            "status": status,
            "assigned_advisor_id": None,
            "assigned_at": now,
            "resolved_at": now,
            "closed_at": now,
            "cancelled_at": now,
            "updated_at": now,
            "version": index,
        }
        for index, status in enumerate(invalid_statuses, start=1)
    ]
    rows.extend(
        [
            {
                "id": "invalid-new",
                "status": "NUEVO",
                "assigned_advisor_id": "stale-advisor",
                "assigned_at": now,
                "resolved_at": now,
                "closed_at": now,
                "cancelled_at": now,
                "updated_at": now,
                "version": 2,
            },
            {
                "id": "valid-new",
                "status": "NUEVO",
                "assigned_advisor_id": None,
                "assigned_at": None,
                "resolved_at": None,
                "closed_at": None,
                "cancelled_at": None,
                "updated_at": now,
                "version": 1,
            },
            {
                "id": "valid-resolved",
                "status": "RESUELTO",
                "assigned_advisor_id": "advisor-1",
                "assigned_at": now,
                "resolved_at": now,
                "closed_at": None,
                "cancelled_at": None,
                "updated_at": now,
                "version": 3,
            },
        ]
    )

    with engine.begin() as connection:
        connection.execute(tickets.insert(), rows)
        connection.execute(
            assignments.insert(),
            [
                {
                    "id": "active-invalid",
                    "ticket_id": "invalid-new",
                    "advisor_id": "stale-advisor",
                    "assigned_by": "supervisor-1",
                    "assigned_at": now,
                    "unassigned_at": None,
                },
                {
                    "id": "active-valid",
                    "ticket_id": "valid-resolved",
                    "advisor_id": "advisor-1",
                    "assigned_by": "supervisor-1",
                    "assigned_at": now,
                    "unassigned_at": None,
                },
            ],
        )
        assert migration._repair_inconsistent_tickets(connection) == 5
        assert migration._repair_inconsistent_tickets(connection) == 0

        repaired = (
            connection.execute(sa.select(tickets).where(tickets.c.id.like("invalid-%")))
            .mappings()
            .all()
        )
        histories = connection.execute(sa.select(history)).mappings().all()
        audits = connection.execute(sa.select(audit)).mappings().all()
        assignment_rows = {
            row["id"]: row
            for row in connection.execute(sa.select(assignments)).mappings().all()
        }
        valid_resolved = (
            connection.execute(
                sa.select(tickets).where(tickets.c.id == "valid-resolved")
            )
            .mappings()
            .one()
        )

    assert len(repaired) == len(histories) == len(audits) == 5
    for ticket in repaired:
        assert ticket["status"] == "NUEVO"
        assert ticket["assigned_advisor_id"] is None
        assert ticket["assigned_at"] is None
        assert ticket["resolved_at"] is None
        assert ticket["closed_at"] is None
        assert ticket["cancelled_at"] is None

    for item in histories:
        assert item["actor_id"] is None
        assert item["old_value"] in (*invalid_statuses, "NUEVO")
        assert item["new_value"] == "NUEVO"
        assert "asesor" in item["description"].casefold()

    for item in audits:
        assert item["actor_user_id"] is None
        assert item["actor_role"] == "SYSTEM"
        assert item["before_data"]["status"] in (*invalid_statuses, "NUEVO")
        assert item["after_data"]["status"] == "NUEVO"
        assert item["dedupe_key"] == (
            f"ticket_assignment_repair:0018:{item['resource_id']}"
        )
        assert "reason" in item["metadata_json"]

    assert valid_resolved["status"] == "RESUELTO"
    assert valid_resolved["assigned_advisor_id"] == "advisor-1"
    assert assignment_rows["active-invalid"]["unassigned_at"] is not None
    assert assignment_rows["active-valid"]["unassigned_at"] is None
