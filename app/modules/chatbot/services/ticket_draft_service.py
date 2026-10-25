import re
from dataclasses import dataclass

from sqlmodel import Session

from app.modules.chatbot.schemas.chatbot import (
    ChatbotTicketDraft,
    ChatbotTicketDraftCategoryOption,
    ChatbotTicketDraftField,
    ChatbotTicketDraftFields,
    ChatbotTicketDraftStatus,
    ChatbotTicketDraftValidationError,
)
from app.modules.conocimiento.models.category import TicketCategory
from app.modules.conocimiento.repositories.category_repository import (
    CategoryRepository,
)
from app.modules.tickets.models.ticket import TicketPriority
from app.shared.text import normalize_text

_FIELD_LABELS = {
    "category_id": r"categor[ií]a|category",
    "subject": r"asunto|t[ií]tulo|subject",
    "description": r"descripci[oó]n|detalle|description",
    "priority": r"prioridad|priority",
}
_ALL_LABELS = "|".join(f"(?:{value})" for value in _FIELD_LABELS.values())
_RESUME_PATTERN = re.compile(
    r"\b(?:contin[uú]a(?:r)?|reanuda(?:r)?|retoma(?:r)?|seguir|prosigue)\b",
    re.IGNORECASE,
)
_CANCEL_PATTERN = re.compile(
    r"\b(?:cancel[aoe]r?|descartar|abandonar)\b"
    r".{0,35}\b(?:borrador|ticket|solicitud|caso)\b|"
    r"\b(?:borrador|ticket|solicitud|caso)\b"
    r".{0,35}\b(?:cancel[aoe]r?|descartar|abandonar)\b",
    re.IGNORECASE,
)
_UNRELATED_QUESTION_PATTERN = re.compile(
    r"^\s*(?:qu[eé]|c[oó]mo|cu[aá]ndo|d[oó]nde|qui[eé]n|cu[aá]l|"
    r"puedo|me puedes|sabes|cu[aá]nto)\b",
    re.IGNORECASE,
)
_CONFIRMATION_WORDS = frozenset(
    {"si", "sí", "ok", "de acuerdo", "listo", "confirmo", "correcto"}
)
_PRIORITY_LABELS = {
    TicketPriority.BAJA: "Baja",
    TicketPriority.MEDIA: "Media",
    TicketPriority.ALTA: "Alta",
    TicketPriority.URGENTE: "Urgente",
}


@dataclass(frozen=True)
class TicketDraftReply:
    draft: ChatbotTicketDraft
    answer: str
    requires_clarification: bool
    offers_ticket: bool
    ready: bool
    cancelled: bool = False


class TicketDraftService:
    """Deterministic multi-turn ticket drafting, isolated from AI providers."""

    def __init__(self, categories: CategoryRepository | None = None) -> None:
        self.categories = categories or CategoryRepository()

    def read(self, session: Session, value: object) -> ChatbotTicketDraft:
        if not isinstance(value, dict):
            return ChatbotTicketDraft()
        try:
            draft = ChatbotTicketDraft.model_validate(value)
        except ValueError:
            return ChatbotTicketDraft()
        if draft.status in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
        }:
            return self._refresh_state(session, draft)
        return self._with_category_options(session, draft)

    def persisted(self, draft: ChatbotTicketDraft) -> dict[str, object]:
        return draft.model_dump(mode="json", exclude={"category_options"})

    def should_handle(
        self,
        session: Session,
        content: str,
        draft: ChatbotTicketDraft,
        *,
        start_requested: bool,
    ) -> bool:
        if draft.status not in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
        }:
            return start_requested or self._has_field_label(content)
        if self._is_cancel(content) or self._is_resume(content):
            return True
        if start_requested:
            return True
        if (
            self._has_field_label(content)
            or normalize_text(content) in _CONFIRMATION_WORDS
        ):
            return True
        if "?" in content or _UNRELATED_QUESTION_PATTERN.search(content):
            return False
        if self._mentions_priority(content):
            return True
        categories = self.categories.list_active(session)
        if self._matching_categories(content, categories):
            return True
        return True

    def handle(
        self,
        session: Session,
        content: str,
        saved_value: object,
        *,
        start_requested: bool,
    ) -> TicketDraftReply:
        existing = self.read(session, saved_value)
        if self._is_cancel(content) and existing.status in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
        }:
            cancelled = ChatbotTicketDraft(status=ChatbotTicketDraftStatus.CANCELLED)
            return TicketDraftReply(
                draft=cancelled,
                answer="El borrador del ticket se canceló. No se creó ningún ticket.",
                requires_clarification=False,
                offers_ticket=False,
                ready=False,
                cancelled=True,
            )

        if existing.status in {
            ChatbotTicketDraftStatus.COLLECTING,
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION,
        }:
            if (
                self._is_resume(content)
                or normalize_text(content) in _CONFIRMATION_WORDS
            ):
                return self._reply(existing, session)
            draft = existing.model_copy(deep=True)
        elif start_requested or self._has_field_label(content):
            draft = ChatbotTicketDraft(
                status=ChatbotTicketDraftStatus.COLLECTING,
                fields=ChatbotTicketDraftFields(priority=TicketPriority.MEDIA),
            )
        else:
            return self._reply(existing, session)

        categories = self.categories.list_active(session)
        errors = {item.field: item.message for item in draft.validation_errors}
        explicit = self._labeled_values(content)

        category_text = explicit.get("category_id")
        category_was_provided = category_text is not None
        category_candidates = self._matching_categories(
            category_text if category_was_provided else content, categories
        )
        if category_candidates or category_was_provided:
            if len(category_candidates) == 1:
                draft.fields.category_id = category_candidates[0].id
                errors.pop(ChatbotTicketDraftField.CATEGORY_ID, None)
            else:
                draft.fields.category_id = None
                if len(category_candidates) > 1:
                    errors[ChatbotTicketDraftField.CATEGORY_ID] = (
                        "Menciona una sola categoría activa para continuar."
                    )
                else:
                    errors[ChatbotTicketDraftField.CATEGORY_ID] = (
                        "Selecciona una de las categorías activas disponibles."
                    )

        priority_text = explicit.get("priority")
        priority_was_provided = priority_text is not None
        priority_candidates = self._matching_priorities(
            priority_text if priority_was_provided else content
        )
        if priority_candidates or priority_was_provided:
            if len(priority_candidates) == 1:
                draft.fields.priority = priority_candidates[0]
                errors.pop(ChatbotTicketDraftField.PRIORITY, None)
            else:
                draft.fields.priority = None
                errors[ChatbotTicketDraftField.PRIORITY] = (
                    "Elige una sola prioridad válida: BAJA, MEDIA, ALTA o URGENTE."
                )

        for field_name, draft_field, enum_field, minimum, maximum in (
            (
                "subject",
                "subject",
                ChatbotTicketDraftField.SUBJECT,
                3,
                200,
            ),
            (
                "description",
                "description",
                ChatbotTicketDraftField.DESCRIPTION,
                5,
                10000,
            ),
        ):
            value = explicit.get(field_name)
            if value is None:
                continue
            value = " ".join(value.split())
            if not minimum <= len(value) <= maximum:
                draft.fields.__setattr__(draft_field, None)
                errors[enum_field] = (
                    f"El campo debe tener entre {minimum} y {maximum} caracteres."
                )
            else:
                draft.fields.__setattr__(draft_field, value)
                errors.pop(enum_field, None)

        if start_requested and draft.fields.description is None:
            incident = self._incident_text(content)
            if incident:
                self._set_text_field(draft, "description", incident, errors)
        elif not explicit and not category_candidates and not priority_candidates:
            next_field = self._next_missing_field(draft)
            if next_field == ChatbotTicketDraftField.CATEGORY_ID:
                errors[ChatbotTicketDraftField.CATEGORY_ID] = (
                    "Selecciona una de las categorías activas disponibles."
                )
            elif next_field == ChatbotTicketDraftField.PRIORITY:
                errors[ChatbotTicketDraftField.PRIORITY] = (
                    "Elige BAJA, MEDIA, ALTA o URGENTE."
                )
            elif next_field in {
                ChatbotTicketDraftField.SUBJECT,
                ChatbotTicketDraftField.DESCRIPTION,
            }:
                self._set_text_field(
                    draft, next_field.value.casefold(), content, errors
                )

        draft.validation_errors = [
            ChatbotTicketDraftValidationError(field=field, message=message)
            for field, message in errors.items()
        ]
        draft = self._refresh_state(session, draft, categories)
        return self._reply(draft, session)

    @staticmethod
    def _is_cancel(content: str) -> bool:
        return bool(_CANCEL_PATTERN.search(content))

    @staticmethod
    def _is_resume(content: str) -> bool:
        return bool(_RESUME_PATTERN.search(content))

    @classmethod
    def is_resume_request(cls, content: str) -> bool:
        return cls._is_resume(content)

    @staticmethod
    def _has_field_label(content: str) -> bool:
        return any(
            re.search(rf"\b(?:{label})\b\s*[:=]", content, re.IGNORECASE)
            for label in _FIELD_LABELS.values()
        )

    @staticmethod
    def _labeled_values(content: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for field_name, label in _FIELD_LABELS.items():
            pattern = re.compile(
                rf"\b(?:{label})\b\s*[:=]\s*(.*?)"
                rf"(?=\s+(?:{_ALL_LABELS})\b\s*[:=]|[;\n]|$)",
                re.IGNORECASE | re.DOTALL,
            )
            match = pattern.search(content)
            if match is not None:
                value = match.group(1).strip()
                if field_name != "description":
                    value = value.strip(".,;:")
                values[field_name] = value
        return values

    @staticmethod
    def _matching_categories(
        content: str, categories: list[TicketCategory]
    ) -> list[TicketCategory]:
        normalized_content = re.sub(r"[\s_-]+", " ", normalize_text(content))
        matches: list[TicketCategory] = []
        for category in categories:
            normalized_name = re.sub(r"[\s_-]+", " ", normalize_text(category.name))
            variants = {normalized_name}
            if normalized_name.endswith("s"):
                variants.add(normalized_name[:-1])
            if any(
                re.search(rf"(?<!\w){re.escape(variant)}(?!\w)", normalized_content)
                for variant in variants
            ):
                matches.append(category)
        return matches

    @staticmethod
    def _matching_priorities(content: str) -> list[TicketPriority]:
        normalized_content = normalize_text(content)
        return [
            priority
            for priority in TicketPriority
            if re.search(
                rf"(?<!\w){priority.value.casefold()}(?!\w)", normalized_content
            )
        ]

    @classmethod
    def _mentions_priority(cls, content: str) -> bool:
        return bool(cls._matching_priorities(content))

    @classmethod
    def _incident_text(cls, content: str) -> str | None:
        without_labeled_fields = content
        for label in _FIELD_LABELS.values():
            without_labeled_fields = re.sub(
                rf"\b(?:{label})\b\s*[:=].*?(?=\s+(?:{_ALL_LABELS})\b\s*[:=]|[;\n]|$)",
                " ",
                without_labeled_fields,
                flags=re.IGNORECASE | re.DOTALL,
            )
        request_start = re.search(
            r"\b(?:ticket|caso|solicitud)\b\s*(?::|por|porque|sobre|debido\s+a)\s*",
            without_labeled_fields,
            re.IGNORECASE,
        )
        if request_start is None:
            return None
        incident = " ".join(without_labeled_fields[request_start.end() :].split())
        incident = re.sub(r"^(?:porque|por|sobre)\s+", "", incident, flags=re.I)
        incident = incident.strip(" .,:;-\t")
        return incident if len(incident) >= 5 else None

    @staticmethod
    def _set_text_field(
        draft: ChatbotTicketDraft,
        field_name: str,
        value: str,
        errors: dict[ChatbotTicketDraftField, str],
    ) -> None:
        clean_value = " ".join(value.split())
        field = (
            ChatbotTicketDraftField.SUBJECT
            if field_name == "subject"
            else ChatbotTicketDraftField.DESCRIPTION
        )
        minimum, maximum = (3, 200) if field_name == "subject" else (5, 10000)
        if not minimum <= len(clean_value) <= maximum:
            setattr(draft.fields, field_name, None)
            errors[field] = (
                f"El campo debe tener entre {minimum} y {maximum} caracteres."
            )
        else:
            setattr(draft.fields, field_name, clean_value)
            errors.pop(field, None)

    @staticmethod
    def _next_missing_field(
        draft: ChatbotTicketDraft,
    ) -> ChatbotTicketDraftField | None:
        if draft.fields.category_id is None:
            return ChatbotTicketDraftField.CATEGORY_ID
        if draft.fields.subject is None:
            return ChatbotTicketDraftField.SUBJECT
        if draft.fields.description is None:
            return ChatbotTicketDraftField.DESCRIPTION
        if draft.fields.priority is None:
            return ChatbotTicketDraftField.PRIORITY
        return None

    def _refresh_state(
        self,
        session: Session,
        draft: ChatbotTicketDraft,
        categories: list[TicketCategory] | None = None,
    ) -> ChatbotTicketDraft:
        categories = (
            categories
            if categories is not None
            else self.categories.list_active(session)
        )
        active_category_ids = {category.id for category in categories}
        errors = {item.field: item.message for item in draft.validation_errors}
        if (
            draft.fields.category_id is not None
            and draft.fields.category_id not in active_category_ids
        ):
            draft.fields.category_id = None
            errors[ChatbotTicketDraftField.CATEGORY_ID] = (
                "La categoría ya no está activa. Selecciona una categoría disponible."
            )
        fields = draft.fields
        missing: list[ChatbotTicketDraftField] = []
        if fields.category_id is None:
            missing.append(ChatbotTicketDraftField.CATEGORY_ID)
        if fields.subject is None:
            missing.append(ChatbotTicketDraftField.SUBJECT)
        if fields.description is None:
            missing.append(ChatbotTicketDraftField.DESCRIPTION)
        if fields.priority is None:
            missing.append(ChatbotTicketDraftField.PRIORITY)
        draft.validation_errors = [
            ChatbotTicketDraftValidationError(field=field, message=message)
            for field, message in errors.items()
        ]
        draft.missing_fields = missing
        draft.status = (
            ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION
            if not missing and not errors
            else ChatbotTicketDraftStatus.COLLECTING
        )
        return self._with_category_options(session, draft, categories)

    def _with_category_options(
        self,
        session: Session,
        draft: ChatbotTicketDraft,
        categories: list[TicketCategory] | None = None,
    ) -> ChatbotTicketDraft:
        if (
            draft.status != ChatbotTicketDraftStatus.COLLECTING
            or draft.fields.category_id is not None
        ):
            draft.category_options = []
            return draft
        categories = (
            categories
            if categories is not None
            else self.categories.list_active(session)
        )
        draft.category_options = [
            ChatbotTicketDraftCategoryOption(id=item.id, name=item.name)
            for item in categories
        ]
        return draft

    def _reply(self, draft: ChatbotTicketDraft, session: Session) -> TicketDraftReply:
        draft = self._with_category_options(session, draft)
        if draft.status == ChatbotTicketDraftStatus.READY_FOR_CONFIRMATION:
            category = next(
                (
                    item
                    for item in self.categories.list_active(session)
                    if item.id == draft.fields.category_id
                ),
                None,
            )
            answer = (
                "Revisa el resumen antes de confirmar: "
                f"categoría {category.name if category else 'seleccionada'}; "
                f"asunto {draft.fields.subject}; "
                f"descripción {draft.fields.description}; "
                f"prioridad {_PRIORITY_LABELS[draft.fields.priority]}. "
                "La creación se realizará solo cuando confirmes en la interfaz."
            )
            return TicketDraftReply(
                draft=draft,
                answer=answer,
                requires_clarification=False,
                offers_ticket=True,
                ready=True,
            )
        if draft.status == ChatbotTicketDraftStatus.CANCELLED:
            return TicketDraftReply(
                draft=draft,
                answer="El borrador está cancelado. No se creó ningún ticket.",
                requires_clarification=False,
                offers_ticket=False,
                ready=False,
                cancelled=True,
            )
        if draft.status == ChatbotTicketDraftStatus.INACTIVE:
            return TicketDraftReply(
                draft=draft,
                answer="No hay un borrador de ticket activo.",
                requires_clarification=False,
                offers_ticket=False,
                ready=False,
            )

        errors = {item.field: item.message for item in draft.validation_errors}
        if ChatbotTicketDraftField.CATEGORY_ID in errors:
            answer = errors[ChatbotTicketDraftField.CATEGORY_ID]
        elif ChatbotTicketDraftField.PRIORITY in errors:
            answer = errors[ChatbotTicketDraftField.PRIORITY]
        elif ChatbotTicketDraftField.SUBJECT in errors:
            answer = errors[ChatbotTicketDraftField.SUBJECT]
        elif ChatbotTicketDraftField.DESCRIPTION in errors:
            answer = errors[ChatbotTicketDraftField.DESCRIPTION]
        elif draft.fields.category_id is None:
            names = ", ".join(item.name for item in draft.category_options)
            answer = f"¿Qué categoría activa corresponde? Puedes elegir: {names}."
        elif draft.fields.subject is None:
            answer = "¿Qué asunto breve deseas colocar en el ticket?"
        elif draft.fields.description is None:
            answer = (
                "Describe el problema o la solicitud con tus propias palabras. "
                "No incluyas contraseñas, OTP, CVV, tokens, códigos de seguridad "
                "ni números completos de tarjetas o cuentas."
            )
        else:
            answer = "Elige una prioridad válida: BAJA, MEDIA, ALTA o URGENTE."
        return TicketDraftReply(
            draft=draft,
            answer=answer,
            requires_clarification=True,
            offers_ticket=False,
            ready=False,
        )
