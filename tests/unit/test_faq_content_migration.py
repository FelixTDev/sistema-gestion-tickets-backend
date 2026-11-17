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
        / "0020_faq_content_cleanup.py"
    )
    assert migration_path.exists(), "FAQ cleanup data migration must be added"
    spec = importlib.util.spec_from_file_location(
        "faq_content_cleanup_migration", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _run_migration(connection, migration, operation: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        getattr(migration, operation)()


def test_faq_cleanup_migration_preserves_ids_and_categories_on_sqlite():
    migration = _load_migration()
    from app.db import models  # noqa: F401
    from app.modules.conocimiento.models.category import TicketCategory
    from app.modules.conocimiento.models.faq import FAQ
    from app.modules.conocimiento.models.version import FAQVersion
    from app.seed.demo_data import seed_demo_data

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)
        seeded = {faq.question: faq for faq in session.exec(select(FAQ)).all()}
        for previous_question, (
            question,
            _answer,
            _keywords,
        ) in migration._FAQ_CONTENT.items():
            if previous_question == "¿Qué requisitos necesito para una tarjeta?":
                continue
            faq = seeded[question]
            faq.title = previous_question
            faq.question = previous_question
            faq.answer = "Información específica de Banco GNB."
            faq.summary = faq.answer
            faq.keywords = "gnb,información específica"
            faq.tags = '["gnb"]'
            faq.synonyms = '["Banco GNB"]'
            faq.normalized_content = "informacion especifica banco gnb"
            faq.normalized_title = "informacion especifica banco gnb"
            faq.normalized_question = faq.normalized_title
            faq.intent = "gnb_specific"
            faq.source_url = "https://www.bancognb.com.pe/inicio/"
            faq.source_verified_at = datetime(2026, 10, 10, tzinfo=UTC)
            snapshots = session.exec(
                select(FAQVersion).where(FAQVersion.faq_id == faq.id)
            ).all()
            for snapshot in snapshots:
                for field in (
                    "title",
                    "question",
                    "answer",
                    "summary",
                    "keywords",
                    "tags",
                    "synonyms",
                    "normalized_content",
                    "intent",
                    "source_url",
                    "source_verified_at",
                ):
                    setattr(snapshot, field, getattr(faq, field))
        faq_ids_before = {row.id for row in session.exec(select(FAQ)).all()}
        faq_categories_before = {
            row.id: row.category_id for row in session.exec(select(FAQ)).all()
        }
        categories_before = {
            row.id: (row.name, row.description, row.is_active)
            for row in session.exec(select(TicketCategory)).all()
        }
        session.commit()

    with engine.connect() as connection, connection.begin():
        _run_migration(connection, migration, "upgrade")

    with Session(engine) as session:
        faqs_after = session.exec(select(FAQ)).all()
        snapshots_after = session.exec(select(FAQVersion)).all()
        faq_after = next(
            row
            for row in faqs_after
            if row.question == "¿Qué tipos de cuentas ofrece una entidad financiera?"
        )
        assert faq_after.answer == (
            "No tengo información verificada para responder esta consulta financiera."
        )
        assert all("gnb" not in str(row.model_dump()).casefold() for row in faqs_after)
        assert all(
            "gnb" not in str(row.model_dump()).casefold() for row in snapshots_after
        )
        assert all(row.source_url is None for row in faqs_after)
        assert all(row.source_verified_at is None for row in faqs_after)
        assert {row.id for row in session.exec(select(FAQ)).all()} == faq_ids_before
        assert {
            row.id: row.category_id for row in session.exec(select(FAQ)).all()
        } == faq_categories_before
        assert {
            row.id: (row.name, row.description, row.is_active)
            for row in session.exec(select(TicketCategory)).all()
        } == categories_before

    with engine.connect() as connection, connection.begin():
        _run_migration(connection, migration, "downgrade")

    with Session(engine) as session:
        faqs_after_downgrade = session.exec(select(FAQ)).all()
        assert all(row.source_url is None for row in faqs_after_downgrade)
        assert {row.id: row.category_id for row in faqs_after_downgrade} == (
            faq_categories_before
        )
        assert all(
            "gnb" not in str(row.model_dump()).casefold()
            for row in faqs_after_downgrade
        )
    engine.dispose()
