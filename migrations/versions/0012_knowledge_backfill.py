"""Backfill normalized FAQ content and initial editorial snapshots."""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0012_knowledge_backfill"
down_revision = "0011_knowledge"
branch_labels = None
depends_on = None


def _normalize(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", without_accents).strip().casefold()


def upgrade() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                """
                SELECT id, category_id, title, question, answer, summary, keywords,
                       tags, synonyms, normalized_content, normalized_title,
                       normalized_question, intent, status, priority, display_order,
                       version, is_active, published_at, unpublished_at, created_by,
                       updated_by, updated_at
                FROM faqs
                """
            )
        ).mappings()
    )
    for row in rows:
        title = row["title"] or row["question"]
        summary = row["summary"] or row["answer"][:1000]
        tags = row["tags"] or "[]"
        synonyms = row["synonyms"] or "[]"
        normalized_content = _normalize(
            " ".join(
                [
                    title,
                    row["question"],
                    row["answer"],
                    summary,
                    row["keywords"],
                    tags,
                    synonyms,
                ]
            )
        )
        normalized_title = _normalize(title)
        normalized_question = _normalize(row["question"])
        connection.execute(
            sa.text(
                """
                UPDATE faqs
                SET title=:title,
                    summary=:summary,
                    tags=:tags,
                    synonyms=:synonyms,
                    normalized_content=:normalized_content,
                    normalized_title=:normalized_title,
                    normalized_question=:normalized_question,
                    version=COALESCE(version, 1),
                    priority=COALESCE(priority, 0),
                    display_order=COALESCE(display_order, 0),
                    status=COALESCE(status, IF(is_active = 1, 'PUBLISHED', 'ARCHIVED')),
                    published_at=CASE
                        WHEN is_active = 1 AND published_at IS NULL THEN created_at
                        ELSE published_at
                    END
                WHERE id=:id
                """
            ),
            {
                "id": row["id"],
                "title": title,
                "summary": summary,
                "tags": tags,
                "synonyms": synonyms,
                "normalized_content": normalized_content,
                "normalized_title": normalized_title,
                "normalized_question": normalized_question,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO faq_versions (
                    id, faq_id, version, category_id, title, question, answer,
                    summary, keywords, tags, synonyms, normalized_content, intent,
                    status, priority, display_order, is_active, published_at,
                    unpublished_at, changed_by, action, changed_at
                )
                SELECT UUID(), id, COALESCE(version, 1), category_id, title,
                       question, answer, summary, keywords, tags, synonyms,
                       normalized_content, intent, status, priority, display_order,
                       is_active, published_at, unpublished_at,
                       COALESCE(updated_by, created_by), 'MIGRATED', updated_at
                FROM faqs
                WHERE id=:id
                  AND NOT EXISTS (
                      SELECT 1 FROM faq_versions v
                      WHERE v.faq_id=faqs.id
                  )
                """
            ),
            {"id": row["id"]},
        )


def downgrade() -> None:
    # Initial snapshots are retained intentionally; removing them would destroy
    # the editorial history of data migrated by this revision.
    pass
