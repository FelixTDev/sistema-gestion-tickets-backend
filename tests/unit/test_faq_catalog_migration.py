import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import Session, SQLModel, create_engine, select


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0021_faq_catalog_refresh.py"
    )
    assert migration_path.exists(), "FAQ catalog data migration must be added"
    spec = importlib.util.spec_from_file_location(
        "faq_catalog_refresh_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _run_migration(connection, migration, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(migration, operation)()


def test_faq_catalog_migration_updates_current_content_and_preserves_history() -> None:
    migration = _load_migration()
    from app.db import models  # noqa: F401
    from app.modules.conocimiento.models.category import TicketCategory
    from app.modules.conocimiento.models.faq import FAQ
    from app.modules.conocimiento.models.version import FAQVersion
    from app.modules.usuarios.models.role import Role
    from app.modules.usuarios.models.user import User
    from app.seed.demo_data import seed_demo_data
    from app.shared.text import normalize_text

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)
        categories = {
            row.name: row for row in session.exec(select(TicketCategory)).all()
        }
        supervisor = session.exec(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(Role.name == "SUPERVISOR")
        ).one()
        for update in migration._FAQ_UPDATES:
            faq = session.exec(
                select(FAQ)
                .where(FAQ.category_id == categories[update["category"]].id)
                .where(FAQ.question == update["question"])
            ).one()
            old_question = next(iter(update["legacy_questions"]), update["question"])
            faq.question = old_question
            faq.title = old_question
            faq.answer = "Respuesta previa a la revisión."
            faq.summary = faq.answer
            faq.keywords = "contenido,anterior"
            faq.tags = '["contenido", "anterior"]'
            faq.normalized_content = "contenido anterior"
            faq.normalized_title = normalize_text(old_question)
            faq.normalized_question = normalize_text(old_question)
            faq.source_url = None
            faq.source_verified_at = None
            session.add(faq)

        old_affiliation_question = "¿Cómo me afilio a Banca Móvil?"
        now = datetime.now(UTC)
        retired = FAQ(
            category_id=categories["BANCA_DIGITAL"].id,
            title=old_affiliation_question,
            question=old_affiliation_question,
            answer="Respuesta previa a la revisión.",
            summary="Respuesta previa a la revisión.",
            keywords="banca,movil,afiliacion",
            tags='["banca", "movil", "afiliacion"]',
            synonyms="[]",
            normalized_content="banca movil afiliacion",
            normalized_title=normalize_text(old_affiliation_question),
            normalized_question=normalize_text(old_affiliation_question),
            status=FAQ.Status.PUBLISHED,
            priority=0,
            display_order=0,
            version=1,
            is_active=True,
            created_by=supervisor.id,
            updated_by=supervisor.id,
            published_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(retired)
        session.flush()
        retired_id = retired.id
        session.add(
            FAQVersion(
                faq_id=retired.id,
                version=retired.version,
                category_id=retired.category_id,
                title=retired.title,
                question=retired.question,
                answer=retired.answer,
                summary=retired.summary,
                keywords=retired.keywords,
                tags=retired.tags,
                synonyms=retired.synonyms,
                normalized_content=retired.normalized_content,
                intent=retired.intent,
                source_url=retired.source_url,
                source_verified_at=retired.source_verified_at,
                status=retired.status,
                priority=retired.priority,
                display_order=retired.display_order,
                is_active=retired.is_active,
                published_at=retired.published_at,
                unpublished_at=retired.unpublished_at,
                changed_by=supervisor.id,
                action="SEEDED",
                changed_at=now,
            )
        )
        faq_ids_before = {row.id for row in session.exec(select(FAQ)).all()}
        categories_before = {
            row.id: (row.name, row.description, row.is_active)
            for row in session.exec(select(TicketCategory)).all()
        }
        old_snapshots = {
            row.id: row.model_dump() for row in session.exec(select(FAQVersion)).all()
        }
        session.commit()

    with engine.connect() as connection, connection.begin():
        _run_migration(connection, migration, "upgrade")

    with Session(engine) as session:
        faqs = session.exec(select(FAQ)).all()
        faq_by_question = {faq.question: faq for faq in faqs if faq.is_active}
        assert len(faqs) == len(faq_ids_before)
        assert {faq.id for faq in faqs} == faq_ids_before
        assert {
            row.id: (row.name, row.description, row.is_active)
            for row in session.exec(select(TicketCategory)).all()
        } == categories_before
        for update in migration._FAQ_UPDATES:
            faq = faq_by_question[update["question"]]
            assert faq.answer == update["answer"]
            assert faq.keywords == update["keywords"]
            assert faq.title == update["question"]
            assert faq.version == 2
        claim = faq_by_question[
            "¿Cuál es la diferencia entre un reclamo y un requerimiento?"
        ]
        assert claim.source_url == migration._SBS_REGULATION_URL
        assert claim.source_verified_at == migration._SBS_REGULATION_VERIFIED_AT

        retired_after = session.get(FAQ, retired_id)
        assert retired_after is not None
        assert retired_after.question == old_affiliation_question
        assert retired_after.status == FAQ.Status.ARCHIVED
        assert retired_after.is_active is False

        snapshots_after = session.exec(select(FAQVersion)).all()
        snapshots_after_by_id = {row.id: row.model_dump() for row in snapshots_after}
        assert all(
            snapshots_after_by_id[snapshot_id] == snapshot
            for snapshot_id, snapshot in old_snapshots.items()
        )
        assert (
            len(snapshots_after) == len(old_snapshots) + len(migration._FAQ_UPDATES) + 1
        )
        assert sum(
            row.action == "MIGRATED_CONTENT_UPDATE" for row in snapshots_after
        ) == len(migration._FAQ_UPDATES)
        assert sum(row.action == "MIGRATED_ARCHIVE" for row in snapshots_after) == 1
        versions_after_first_run = {row.id: row.model_dump() for row in snapshots_after}

    with engine.connect() as connection, connection.begin():
        _run_migration(connection, migration, "upgrade")

    with Session(engine) as session:
        assert {
            row.id: row.model_dump() for row in session.exec(select(FAQVersion)).all()
        } == versions_after_first_run

    engine.dispose()
