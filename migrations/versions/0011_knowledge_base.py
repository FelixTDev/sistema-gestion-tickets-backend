"""Evolve FAQs into editorial knowledge with versions and feedback."""

import sqlalchemy as sa
from alembic import op

revision = "0011_knowledge"
down_revision = "0010_assignment_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    faq_status = sa.Enum("DRAFT", "REVIEW", "PUBLISHED", "ARCHIVED", name="faqstatus")
    op.add_column("faqs", sa.Column("title", sa.String(200), nullable=True))
    op.add_column("faqs", sa.Column("summary", sa.String(1000), nullable=True))
    op.add_column("faqs", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column("faqs", sa.Column("synonyms", sa.Text(), nullable=True))
    op.add_column("faqs", sa.Column("normalized_content", sa.Text(), nullable=True))
    op.add_column("faqs", sa.Column("normalized_title", sa.String(200), nullable=True))
    op.add_column("faqs", sa.Column("normalized_question", sa.Text(), nullable=True))
    op.add_column("faqs", sa.Column("intent", sa.String(100), nullable=True))
    op.add_column("faqs", sa.Column("status", faq_status, nullable=True))
    op.add_column("faqs", sa.Column("priority", sa.Integer(), nullable=True))
    op.add_column("faqs", sa.Column("display_order", sa.Integer(), nullable=True))
    op.add_column("faqs", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column(
        "faqs",
        sa.Column(
            "updated_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
    )
    op.add_column(
        "faqs", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "faqs", sa.Column("unpublished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        sa.text(
            """
            UPDATE faqs
            SET title = question,
                summary = LEFT(answer, 1000),
                tags = '[]',
                synonyms = '[]',
                normalized_content = LOWER(CONCAT_WS(' ', question, answer, keywords)),
                normalized_title = LOWER(question),
                normalized_question = LOWER(question),
                status = IF(is_active = 1, 'PUBLISHED', 'ARCHIVED'),
                priority = 0,
                display_order = 0,
                version = 1,
                published_at = IF(is_active = 1, created_at, NULL)
            """
        )
    )
    op.alter_column("faqs", "title", existing_type=sa.String(200), nullable=False)
    op.alter_column("faqs", "summary", existing_type=sa.String(1000), nullable=False)
    op.alter_column("faqs", "tags", existing_type=sa.Text(), nullable=False)
    op.alter_column("faqs", "synonyms", existing_type=sa.Text(), nullable=False)
    op.alter_column(
        "faqs", "normalized_content", existing_type=sa.Text(), nullable=False
    )
    op.alter_column(
        "faqs", "normalized_title", existing_type=sa.String(200), nullable=False
    )
    op.alter_column(
        "faqs", "normalized_question", existing_type=sa.Text(), nullable=False
    )
    op.alter_column("faqs", "status", existing_type=faq_status, nullable=False)
    op.alter_column("faqs", "priority", existing_type=sa.Integer(), nullable=False)
    op.alter_column("faqs", "display_order", existing_type=sa.Integer(), nullable=False)
    op.alter_column("faqs", "version", existing_type=sa.Integer(), nullable=False)
    op.create_index(
        "ix_faqs_editorial_scope",
        "faqs",
        ["status", "is_active", "category_id", "published_at"],
    )
    op.create_index("ix_faqs_updated_at", "faqs", ["updated_at"])

    op.create_table(
        "faq_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("faq_id", sa.String(36), sa.ForeignKey("faqs.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "category_id",
            sa.String(36),
            sa.ForeignKey("ticket_categories.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.Column("tags", sa.Text(), nullable=False),
        sa.Column("synonyms", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(100), nullable=True),
        sa.Column("status", faq_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unpublished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "changed_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("faq_id", "version", name="uq_faq_versions_faq_version"),
    )
    op.create_index(
        "ix_faq_versions_faq_version", "faq_versions", ["faq_id", "version"]
    )
    op.create_table(
        "faq_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("faq_id", sa.String(36), sa.ForeignKey("faqs.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_helpful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_faq_feedback_faq_created", "faq_feedback", ["faq_id", "created_at"]
    )
    op.create_index(
        "ix_faq_feedback_faq_helpful", "faq_feedback", ["faq_id", "is_helpful"]
    )


def downgrade() -> None:
    op.drop_index("ix_faq_feedback_faq_helpful", table_name="faq_feedback")
    op.drop_index("ix_faq_feedback_faq_created", table_name="faq_feedback")
    op.drop_table("faq_feedback")
    op.drop_index("ix_faq_versions_faq_version", table_name="faq_versions")
    op.drop_table("faq_versions")
    op.drop_index("ix_faqs_updated_at", table_name="faqs")
    op.drop_index("ix_faqs_editorial_scope", table_name="faqs")
    for column in (
        "unpublished_at",
        "published_at",
        "updated_by",
        "version",
        "display_order",
        "priority",
        "status",
        "intent",
        "normalized_question",
        "normalized_title",
        "normalized_content",
        "synonyms",
        "tags",
        "summary",
        "title",
    ):
        op.drop_column("faqs", column)
