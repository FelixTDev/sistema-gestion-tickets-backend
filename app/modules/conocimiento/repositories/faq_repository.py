from datetime import UTC, datetime

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.conocimiento.models.version import FAQVersion


class FAQRepository:
    def list_active(self, session: Session) -> list[FAQ]:
        _, items = self.list_public_page(session, page=1, page_size=10000)
        return items

    def get_active_by_id(self, session: Session, faq_id: str) -> FAQ | None:
        conditions = self._public_conditions(datetime.now(UTC))
        return session.exec(
            select(FAQ)
            .join(TicketCategory, TicketCategory.id == FAQ.category_id)
            .where(FAQ.id == faq_id, *conditions)
        ).first()

    def list_public_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        category_id: str | None = None,
        tag: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
    ) -> tuple[int, list[FAQ]]:
        now = datetime.now(UTC)
        conditions = self._public_conditions(now)
        conditions.extend(
            self._search_conditions(
                search=search,
                category_id=category_id,
                tag=tag,
                published_from=published_from,
                published_to=published_to,
            )
        )
        base = select(FAQ).join(TicketCategory, TicketCategory.id == FAQ.category_id)
        score = self._relevance(search)
        count = session.exec(
            select(func.count(FAQ.id))
            .select_from(FAQ)
            .join(TicketCategory, TicketCategory.id == FAQ.category_id)
            .where(*conditions)
        ).one()
        order_by = [
            score.desc(),
            FAQ.priority.desc(),
            FAQ.display_order.asc(),
            FAQ.published_at.desc(),
            FAQ.updated_at.desc(),
            FAQ.id.asc(),
        ]
        items = list(
            session.exec(
                base.where(*conditions)
                .order_by(*order_by)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(count), items

    def list_admin_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        status: FAQ.Status | None = None,
        search: str | None = None,
        category_id: str | None = None,
        tag: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> tuple[int, list[FAQ]]:
        conditions = self._search_conditions(
            search=search,
            category_id=category_id,
            tag=tag,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
        )
        if updated_to is not None:
            conditions.append(FAQ.updated_at <= updated_to)
        if status is not None:
            conditions.append(FAQ.status == status)
        base = select(FAQ).join(TicketCategory, TicketCategory.id == FAQ.category_id)
        count = session.exec(
            select(func.count(FAQ.id))
            .select_from(FAQ)
            .join(TicketCategory, TicketCategory.id == FAQ.category_id)
            .where(*conditions)
        ).one()
        items = list(
            session.exec(
                base.where(*conditions)
                .order_by(
                    FAQ.updated_at.desc(),
                    FAQ.created_at.desc(),
                    FAQ.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return int(count), items

    def get(self, session: Session, faq_id: str) -> FAQ | None:
        return session.get(FAQ, faq_id)

    def duplicate(
        self,
        session: Session,
        *,
        normalized_question: str,
        normalized_title: str,
        exclude_id: str | None = None,
    ) -> FAQ | None:
        conditions = [
            or_(
                FAQ.normalized_question == normalized_question,
                FAQ.normalized_title == normalized_title,
            )
        ]
        if exclude_id is not None:
            conditions.append(FAQ.id != exclude_id)
        return session.exec(select(FAQ).where(*conditions)).first()

    def add(self, session: Session, faq: FAQ) -> FAQ:
        session.add(faq)
        session.flush()
        return faq

    def add_version(self, session: Session, version: FAQVersion) -> FAQVersion:
        session.add(version)
        session.flush()
        return version

    def versions(self, session: Session, faq_id: str) -> list[FAQVersion]:
        return list(
            session.exec(
                select(FAQVersion)
                .where(FAQVersion.faq_id == faq_id)
                .order_by(FAQVersion.version.desc())
            ).all()
        )

    def add_feedback(self, session: Session, feedback: FAQFeedback) -> FAQFeedback:
        session.add(feedback)
        session.flush()
        return feedback

    def feedback_metrics(self, session: Session) -> tuple[int, int]:
        helpful = case((FAQFeedback.is_helpful, 1), else_=0)
        total, helpful_count = session.exec(
            select(func.count(FAQFeedback.id), func.sum(helpful))
        ).one()
        return int(total or 0), int(helpful_count or 0)

    @staticmethod
    def _public_conditions(now: datetime) -> list[object]:
        return [
            FAQ.status == FAQ.Status.PUBLISHED,
            FAQ.is_active,
            TicketCategory.is_active,
            or_(FAQ.published_at.is_(None), FAQ.published_at <= now),
            or_(FAQ.unpublished_at.is_(None), FAQ.unpublished_at > now),
        ]

    @staticmethod
    def _search_conditions(
        *,
        search: str | None = None,
        category_id: str | None = None,
        tag: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> list[object]:
        conditions: list[object] = []
        if category_id is not None:
            conditions.append(FAQ.category_id == category_id)
        if tag:
            conditions.append(FAQ.tags.ilike(f"%{tag}%"))
        if search:
            pattern = f"%{search}%"
            conditions.append(
                or_(
                    FAQ.title.ilike(pattern),
                    FAQ.question.ilike(pattern),
                    FAQ.answer.ilike(pattern),
                    FAQ.summary.ilike(pattern),
                    FAQ.keywords.ilike(pattern),
                    FAQ.tags.ilike(pattern),
                    FAQ.synonyms.ilike(pattern),
                    FAQ.normalized_content.ilike(pattern),
                    TicketCategory.name.ilike(pattern),
                )
            )
        if published_from is not None:
            conditions.append(FAQ.published_at >= published_from)
        if published_to is not None:
            conditions.append(FAQ.published_at <= published_to)
        if created_from is not None:
            conditions.append(FAQ.created_at >= created_from)
        if created_to is not None:
            conditions.append(FAQ.created_at <= created_to)
        if updated_from is not None:
            conditions.append(FAQ.updated_at >= updated_from)
        if updated_to is not None:
            conditions.append(FAQ.updated_at <= updated_to)
        return conditions

    @staticmethod
    def _relevance(search: str | None):
        if not search:
            return case((FAQ.priority >= 0, 0), else_=0)
        pattern = f"%{search}%"
        return case(
            (FAQ.title.ilike(pattern), 7),
            (FAQ.question.ilike(pattern), 6),
            (FAQ.summary.ilike(pattern), 5),
            (FAQ.answer.ilike(pattern), 4),
            (FAQ.keywords.ilike(pattern), 3),
            (FAQ.tags.ilike(pattern), 2),
            (FAQ.synonyms.ilike(pattern), 2),
            (FAQ.normalized_content.ilike(pattern), 1),
            else_=0,
        )
