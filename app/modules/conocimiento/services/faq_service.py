import html
import json
from datetime import UTC, datetime
from math import ceil

from fastapi import HTTPException
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.models.faq import FAQ
from app.modules.conocimiento.models.feedback import FAQFeedback
from app.modules.conocimiento.models.version import FAQVersion
from app.modules.conocimiento.repositories.category_repository import CategoryRepository
from app.modules.conocimiento.repositories.faq_repository import FAQRepository
from app.modules.conocimiento.schemas.faq import (
    FAQCreate,
    FAQFeedbackCreate,
    FAQStatus,
    FAQUpdate,
    WorkflowStatusUpdate,
)
from app.shared.datetime import as_utc
from app.shared.pagination import PaginationResult
from app.shared.text import normalize_text


class FAQService:
    def __init__(
        self,
        repository: FAQRepository | None = None,
        category_repository: CategoryRepository | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.repository = repository or FAQRepository()
        self.categories = category_repository or CategoryRepository()
        self.audit = audit_service or AuditService()

    def list_active(
        self,
        session: Session,
        *,
        page: int | None = None,
        page_size: int | None = None,
        search: str | None = None,
        category_id: str | None = None,
        tag: str | None = None,
        published_from: datetime | None = None,
        published_to: datetime | None = None,
    ) -> list[FAQ] | PaginationResult[FAQ]:
        self._validate_range(
            published_from, published_to, "published_from", "published_to"
        )
        normalized_search = normalize_text(search) if search else None
        normalized_tag = normalize_text(tag) if tag else None
        if page is None and page_size is None:
            _, items = self.repository.list_public_page(
                session,
                page=1,
                page_size=10000,
                search=normalized_search,
                category_id=category_id,
                tag=normalized_tag,
                published_from=published_from,
                published_to=published_to,
            )
            return items
        current_page = page or 1
        current_page_size = page_size or 20
        total, items = self.repository.list_public_page(
            session,
            page=current_page,
            page_size=current_page_size,
            search=normalized_search,
            category_id=category_id,
            tag=normalized_tag,
            published_from=published_from,
            published_to=published_to,
        )
        return PaginationResult(
            page=current_page,
            page_size=current_page_size,
            total=total,
            total_pages=ceil(total / current_page_size) if total else 0,
            items=items,
        )

    def list_admin(
        self,
        session: Session,
        actor: AuthenticatedUser,
        *,
        page: int,
        page_size: int,
        status: FAQStatus | None = None,
        search: str | None = None,
        category_id: str | None = None,
        tag: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        updated_from: datetime | None = None,
        updated_to: datetime | None = None,
    ) -> PaginationResult[FAQ]:
        self._require_supervisor(actor)
        self._validate_range(created_from, created_to, "created_from", "created_to")
        self._validate_range(updated_from, updated_to, "updated_from", "updated_to")
        total, items = self.repository.list_admin_page(
            session,
            page=page,
            page_size=page_size,
            status=status,
            search=normalize_text(search) if search else None,
            category_id=category_id,
            tag=normalize_text(tag) if tag else None,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
            updated_to=updated_to,
        )
        return PaginationResult(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
            items=items,
        )

    def get_active(self, session: Session, faq_id: str) -> FAQ:
        faq = self.repository.get_active_by_id(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        return faq

    def create(
        self, session: Session, data: FAQCreate, actor: AuthenticatedUser
    ) -> FAQ:
        self._require_supervisor(actor)
        category = self._ensure_category(session, data.category_id)
        values = self._content_values(data, category)
        self._ensure_not_duplicate(
            session, values["normalized_question"], values["normalized_title"]
        )
        now = datetime.now(UTC)
        faq = FAQ(
            **values,
            category_id=category.id,
            status=FAQStatus.DRAFT,
            is_active=False,
            created_by=actor.user.id,
            updated_by=actor.user.id,
            created_at=now,
            updated_at=now,
        )
        self.repository.add(session, faq)
        self._snapshot(session, faq, actor.user.id, "CREATED")
        self.audit.record(
            session,
            event_type="KNOWLEDGE",
            action="CREATED",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="FAQ",
            resource_id=faq.id,
            success=True,
            after_data={"version": faq.version, "status": faq.status.value},
        )
        session.commit()
        session.refresh(faq)
        return faq

    def update(
        self, session: Session, faq_id: str, data: FAQUpdate, actor: AuthenticatedUser
    ) -> FAQ:
        self._require_supervisor(actor)
        faq = self._get(session, faq_id)
        values = data.model_dump(exclude_unset=True)
        category = self._ensure_category(
            session, values.get("category_id", faq.category_id)
        )
        current = self._faq_values(faq, category)
        merged = {**current, **values}
        merged["category_id"] = category.id
        merged_values = self._content_values_from_values(merged)
        self._ensure_not_duplicate(
            session,
            merged_values["normalized_question"],
            merged_values["normalized_title"],
            exclude_id=faq.id,
        )
        content_changed = any(
            field in values
            for field in {
                "category_id",
                "title",
                "question",
                "answer",
                "summary",
                "keywords",
                "tags",
                "synonyms",
                "intent",
                "priority",
                "display_order",
            }
        )
        for field, value in merged_values.items():
            setattr(faq, field, value)
        faq.category_id = category.id
        now = datetime.now(UTC)
        if faq.status == FAQStatus.PUBLISHED and content_changed:
            faq.status = FAQStatus.REVIEW
            faq.is_active = False
            faq.unpublished_at = now
        faq.version += 1
        faq.updated_by = actor.user.id
        faq.updated_at = now
        session.add(faq)
        self._snapshot(session, faq, actor.user.id, "UPDATED")
        self.audit.record(
            session,
            event_type="KNOWLEDGE",
            action="UPDATED",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="FAQ",
            resource_id=faq.id,
            success=True,
            after_data={"version": faq.version, "status": faq.status.value},
        )
        session.commit()
        session.refresh(faq)
        return faq

    def workflow(
        self,
        session: Session,
        faq_id: str,
        data: WorkflowStatusUpdate,
        actor: AuthenticatedUser,
    ) -> FAQ:
        self._require_supervisor(actor)
        faq = self._get(session, faq_id)
        target = data.status
        allowed = {
            FAQStatus.DRAFT: {FAQStatus.REVIEW, FAQStatus.ARCHIVED},
            FAQStatus.REVIEW: {
                FAQStatus.DRAFT,
                FAQStatus.PUBLISHED,
                FAQStatus.ARCHIVED,
            },
            FAQStatus.PUBLISHED: {FAQStatus.REVIEW, FAQStatus.ARCHIVED},
            FAQStatus.ARCHIVED: {FAQStatus.DRAFT},
        }
        if target == faq.status or target not in allowed[faq.status]:
            raise HTTPException(status_code=409, detail="Transición editorial inválida")
        now = datetime.now(UTC)
        if target == FAQStatus.PUBLISHED:
            self._ensure_publishable(session, faq)
            faq.is_active = True
            faq.published_at = now
            faq.unpublished_at = None
        else:
            if faq.status == FAQStatus.PUBLISHED or target == FAQStatus.ARCHIVED:
                faq.unpublished_at = now
            faq.is_active = False
        faq.status = target
        faq.version += 1
        faq.updated_by = actor.user.id
        faq.updated_at = now
        session.add(faq)
        self._snapshot(session, faq, actor.user.id, f"STATUS_{target.value}")
        self.audit.record(
            session,
            event_type="KNOWLEDGE",
            action=f"STATUS_{target.value}",
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="FAQ",
            resource_id=faq.id,
            success=True,
            before_data={"status": faq.status.value},
            after_data={"status": target.value, "version": faq.version},
        )
        session.commit()
        session.refresh(faq)
        return faq

    def set_status(
        self, session: Session, faq_id: str, is_active: bool, actor: AuthenticatedUser
    ) -> FAQ:
        target = FAQStatus.PUBLISHED if is_active else FAQStatus.ARCHIVED
        return self.workflow(
            session,
            faq_id,
            WorkflowStatusUpdate(status=target),
            actor,
        )

    def history(
        self, session: Session, faq_id: str, actor: AuthenticatedUser
    ) -> list[FAQVersion]:
        self._require_supervisor(actor)
        self._get(session, faq_id)
        return self.repository.versions(session, faq_id)

    def add_feedback(
        self,
        session: Session,
        faq_id: str,
        data: FAQFeedbackCreate,
        actor: AuthenticatedUser | None,
    ) -> FAQFeedback:
        if actor is not None and actor.role != "CLIENTE":
            raise HTTPException(status_code=403, detail="Feedback no autorizado")
        faq = self.get_active(session, faq_id)
        comment = self._sanitize_optional(data.comment)
        feedback = FAQFeedback(
            faq_id=faq.id,
            user_id=actor.user.id if actor else None,
            is_helpful=data.is_helpful,
            comment=comment,
        )
        self.repository.add_feedback(session, feedback)
        self.audit.record(
            session,
            event_type="KNOWLEDGE",
            action="FEEDBACK_RECEIVED",
            actor_user_id=actor.user.id if actor else None,
            actor_role=actor.role if actor else None,
            resource_type="FAQ",
            resource_id=faq.id,
            target_user_id=actor.user.id if actor else None,
            success=True,
            metadata={"is_helpful": data.is_helpful},
        )
        session.commit()
        session.refresh(feedback)
        return feedback

    def utility_metrics(
        self, session: Session, actor: AuthenticatedUser
    ) -> dict[str, int | float]:
        self._require_supervisor(actor)
        total, helpful = self.repository.feedback_metrics(session)
        return {
            "total_feedback": total,
            "helpful": helpful,
            "not_helpful": total - helpful,
            "usefulness_rate": round(helpful / total * 100, 2) if total else 0.0,
        }

    def _ensure_category(self, session: Session, category_id: str) -> TicketCategory:
        category = self.categories.get(session, category_id)
        if category is None or not category.is_active:
            raise HTTPException(
                status_code=422, detail="La categoría no existe o está inactiva"
            )
        return category

    def _get(self, session: Session, faq_id: str) -> FAQ:
        faq = self.repository.get(session, faq_id)
        if faq is None:
            raise HTTPException(status_code=404, detail="FAQ no encontrada")
        return faq

    def _ensure_publishable(self, session: Session, faq: FAQ) -> None:
        self._ensure_category(session, faq.category_id)
        if not faq.question.strip() or not faq.answer.strip():
            raise HTTPException(
                status_code=422, detail="La FAQ necesita pregunta y respuesta"
            )

    def _ensure_not_duplicate(
        self,
        session: Session,
        normalized_question: str,
        normalized_title: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        duplicate = self.repository.duplicate(
            session,
            normalized_question=normalized_question,
            normalized_title=normalized_title,
            exclude_id=exclude_id,
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Ya existe contenido con el mismo título o pregunta",
            )

    @staticmethod
    def _content_values(data: FAQCreate, category: TicketCategory) -> dict[str, object]:
        values = data.model_dump()
        values["category_name"] = category.name
        return FAQService._content_values_from_values(values)

    @staticmethod
    def _content_values_from_values(values: dict[str, object]) -> dict[str, object]:
        question_value = values.get("question")
        answer_value = values.get("answer")
        keywords_value = values.get("keywords")
        question = FAQService._sanitize(
            question_value if isinstance(question_value, str) else ""
        )
        answer = FAQService._sanitize(
            answer_value if isinstance(answer_value, str) else ""
        )
        title = FAQService._sanitize(str(values.get("title") or question))
        summary = FAQService._sanitize(str(values.get("summary") or answer[:1000]))
        keywords = FAQService._sanitize(
            keywords_value if isinstance(keywords_value, str) else ""
        )
        tags = FAQService._clean_terms(values.get("tags", []))
        synonyms = FAQService._clean_terms(values.get("synonyms", []))
        intent = FAQService._sanitize_optional(values.get("intent"))
        normalized_question = normalize_text(question)
        normalized_title = normalize_text(title)
        normalized_content = normalize_text(
            " ".join(
                [
                    title,
                    question,
                    answer,
                    summary,
                    keywords,
                    *tags,
                    *synonyms,
                    str(values.get("category_name", "")),
                ]
            )
        )
        return {
            "title": title,
            "question": question,
            "answer": answer,
            "summary": summary,
            "keywords": keywords,
            "tags": json.dumps(tags, ensure_ascii=False),
            "synonyms": json.dumps(synonyms, ensure_ascii=False),
            "normalized_content": normalized_content,
            "normalized_title": normalized_title,
            "normalized_question": normalized_question,
            "intent": intent,
            "priority": int(values.get("priority", 0)),
            "display_order": int(values.get("display_order", 0)),
        }

    @staticmethod
    def _faq_values(faq: FAQ, category: TicketCategory) -> dict[str, object]:
        return {
            "category_id": category.id,
            "category_name": category.name,
            "title": faq.title,
            "question": faq.question,
            "answer": faq.answer,
            "summary": faq.summary,
            "keywords": faq.keywords,
            "tags": faq.tags,
            "synonyms": faq.synonyms,
            "intent": faq.intent,
            "priority": faq.priority,
            "display_order": faq.display_order,
        }

    @staticmethod
    def _clean_terms(value: object) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = [term for term in value.split(",") if term.strip()]
        if not isinstance(value, list):
            raise HTTPException(status_code=422, detail="Términos inválidos")
        terms: list[str] = []
        seen: set[str] = set()
        for term in value:
            cleaned = FAQService._sanitize(str(term))
            key = normalize_text(cleaned)
            if key and key not in seen:
                seen.add(key)
                terms.append(cleaned)
        return terms

    @staticmethod
    def _sanitize(value: str) -> str:
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            raise HTTPException(
                status_code=422, detail="El contenido no puede estar vacío"
            )
        return html.escape(cleaned, quote=False)

    @staticmethod
    def _sanitize_optional(value: object) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(str(value).split()).strip()
        return html.escape(cleaned, quote=False) if cleaned else None

    @staticmethod
    def _snapshot(session: Session, faq: FAQ, actor_id: str, action: str) -> FAQVersion:
        snapshot = FAQVersion(
            faq_id=faq.id,
            version=faq.version,
            category_id=faq.category_id,
            title=faq.title,
            question=faq.question,
            answer=faq.answer,
            summary=faq.summary,
            keywords=faq.keywords,
            tags=faq.tags,
            synonyms=faq.synonyms,
            normalized_content=faq.normalized_content,
            intent=faq.intent,
            status=faq.status,
            priority=faq.priority,
            display_order=faq.display_order,
            is_active=faq.is_active,
            published_at=faq.published_at,
            unpublished_at=faq.unpublished_at,
            changed_by=actor_id,
            action=action,
        )
        session.add(snapshot)
        session.flush()
        return snapshot

    @staticmethod
    def _validate_range(
        start: datetime | None,
        end: datetime | None,
        start_name: str,
        end_name: str,
    ) -> None:
        if start is not None and end is not None and as_utc(start) > as_utc(end):
            raise HTTPException(
                status_code=422,
                detail=f"{start_name} debe ser menor o igual que {end_name}",
            )

    @staticmethod
    def _require_supervisor(actor: AuthenticatedUser) -> None:
        if actor.role != "SUPERVISOR":
            raise HTTPException(
                status_code=403,
                detail="Solo un supervisor puede realizar esta operación",
            )
