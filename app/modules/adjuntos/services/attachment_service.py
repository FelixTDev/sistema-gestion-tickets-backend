import re
import unicodedata
from datetime import UTC, datetime
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlmodel import Session

from app.api.deps import AuthenticatedUser
from app.core.config import get_settings
from app.modules.adjuntos.models.attachment import Attachment, AttachmentStatus
from app.modules.adjuntos.repositories.attachment_repository import (
    AttachmentRepository,
)
from app.modules.adjuntos.services.storage_provider import (
    LocalStorageProvider,
    StorageNotFoundError,
    StorageProvider,
    StorageTooLargeError,
)
from app.modules.auditoria.services.audit_service import AuditService
from app.modules.tickets.models.history import TicketHistory
from app.modules.tickets.models.ticket import Ticket

_EXTENSION_TO_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".txt": "text/plain",
}
_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    "COM1",
    "COM2",
    "COM3",
    "LPT1",
    "LPT2",
    "LPT3",
}


class AttachmentInputError(ValueError):
    pass


class AttachmentMimeError(ValueError):
    pass


class AttachmentTooLargeError(ValueError):
    pass


class AttachmentService:
    def __init__(
        self,
        repository: AttachmentRepository | None = None,
        storage_provider: StorageProvider | None = None,
        *,
        max_file_size: int | None = None,
        max_total_size_per_ticket: int | None = None,
        max_files_per_ticket: int | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        settings = get_settings()
        self.repository = repository or AttachmentRepository()
        self.audit = audit_service or AuditService()
        if storage_provider is None:
            storage_provider = LocalStorageProvider(settings.attachment_storage_dir)
        self.storage = storage_provider
        self.max_file_size = (
            max_file_size
            if max_file_size is not None
            else settings.attachment_max_file_size_bytes
        )
        self.max_total_size_per_ticket = (
            max_total_size_per_ticket
            if max_total_size_per_ticket is not None
            else settings.attachment_max_total_size_per_ticket_bytes
        )
        self.max_files_per_ticket = (
            max_files_per_ticket
            if max_files_per_ticket is not None
            else settings.attachment_max_files_per_ticket
        )
        self.allowed_extensions = {
            extension.casefold() for extension in settings.attachment_allowed_extensions
        }
        self.allowed_mime_types = {
            mime.casefold() for mime in settings.attachment_allowed_mime_types
        }

    def upload(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        upload: UploadFile,
        comment_id: str | None = None,
    ) -> Attachment:
        ticket = self._authorized_ticket(session, ticket_id, actor)
        if comment_id is not None:
            comment = self.repository.get_comment(session, comment_id)
            if comment is None or comment.ticket_id != ticket.id:
                raise HTTPException(status_code=404, detail="Comentario no encontrado")

        filename = self._sanitize_filename(upload.filename)
        extension = PurePath(filename).suffix.casefold()
        declared_mime = self._declared_mime(upload.content_type)
        self._validate_declared_type(extension, declared_mime)
        if self.repository.count_active_for_ticket(session, ticket.id) >= (
            self.max_files_per_ticket
        ):
            raise AttachmentTooLargeError("Se alcanzó el límite de archivos del ticket")

        storage_key = f"attachments/{uuid4().hex}"
        try:
            stored = self.storage.store(upload.file, storage_key, self.max_file_size)
        except StorageTooLargeError as error:
            raise AttachmentTooLargeError(
                "El archivo excede el tamaño permitido"
            ) from error

        try:
            if stored.file_size == 0:
                raise AttachmentInputError("El archivo no puede estar vacío")
            if stored.detected_mime is None:
                raise AttachmentInputError("No se pudo detectar el tipo del archivo")
            if stored.detected_mime.casefold() not in self.allowed_mime_types:
                raise AttachmentMimeError("El contenido del archivo no está permitido")
            if stored.detected_mime.casefold() != declared_mime:
                raise AttachmentMimeError(
                    "El MIME declarado no coincide con el contenido del archivo"
                )
            if _EXTENSION_TO_MIME.get(extension) != stored.detected_mime.casefold():
                raise AttachmentMimeError(
                    "La extensión no coincide con el contenido del archivo"
                )
            current_total = self.repository.total_size_active_for_ticket(
                session, ticket.id
            )
            if current_total + stored.file_size > self.max_total_size_per_ticket:
                raise AttachmentTooLargeError(
                    "Se alcanzó la cuota total de adjuntos del ticket"
                )

            attachment = Attachment(
                ticket_id=ticket.id,
                comment_id=comment_id,
                uploaded_by_user_id=actor.user.id,
                original_filename=filename,
                storage_key=stored.storage_key,
                mime_type_declared=declared_mime,
                mime_type_detected=stored.detected_mime,
                file_size=stored.file_size,
                sha256=stored.sha256,
            )
            self.repository.add(session, attachment)
            self._history(
                session, ticket.id, actor, "ATTACHMENT_UPLOADED", attachment.id
            )
            session.commit()
            session.refresh(attachment)
            return attachment
        except Exception:
            session.rollback()
            try:
                self.storage.delete(storage_key)
            except Exception:
                pass
            raise

    def list_for_ticket(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> list[Attachment]:
        ticket = self._authorized_ticket(session, ticket_id, actor)
        return self.repository.list_active_for_ticket(session, ticket.id)

    def download(
        self, session: Session, attachment_id: str, actor: AuthenticatedUser
    ) -> tuple[Attachment, BinaryIO]:
        attachment = self.repository.get(session, attachment_id)
        if attachment is None or attachment.status != AttachmentStatus.ACTIVE:
            raise HTTPException(status_code=404, detail="Adjunto no encontrado")
        self._authorized_ticket(session, attachment.ticket_id, actor)
        try:
            stream = self.storage.open(attachment.storage_key)
        except StorageNotFoundError as error:
            raise HTTPException(
                status_code=404, detail="Adjunto no encontrado"
            ) from error
        return attachment, stream

    def delete(
        self, session: Session, attachment_id: str, actor: AuthenticatedUser
    ) -> Attachment:
        attachment = self.repository.get(session, attachment_id)
        if attachment is None:
            raise HTTPException(status_code=404, detail="Adjunto no encontrado")
        self._authorized_ticket(session, attachment.ticket_id, actor)
        if attachment.status == AttachmentStatus.DELETED:
            return attachment
        deleted_at = datetime.now(UTC)
        self.repository.mark_deleted(attachment, deleted_at)
        self._history(
            session, attachment.ticket_id, actor, "ATTACHMENT_DELETED", attachment.id
        )
        session.add(attachment)
        session.commit()
        session.refresh(attachment)
        try:
            self.storage.delete(attachment.storage_key)
        except Exception:
            # The resource is already inaccessible; metadata remains auditable.
            pass
        return attachment

    def _authorized_ticket(
        self, session: Session, ticket_id: str, actor: AuthenticatedUser
    ) -> Ticket:
        ticket = self.repository.get_ticket(session, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket no encontrado")
        if actor.role == "CLIENTE" and ticket.client_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role == "ASESOR" and ticket.assigned_advisor_id != actor.user.id:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        if actor.role not in {"CLIENTE", "ASESOR", "SUPERVISOR"}:
            raise HTTPException(status_code=403, detail="Ticket no autorizado")
        return ticket

    @staticmethod
    def _sanitize_filename(filename: str | None) -> str:
        if not filename or "\x00" in filename:
            raise AttachmentInputError("El nombre del archivo no es válido")
        normalized = unicodedata.normalize("NFKC", filename).replace("\\", "/")
        if "/" in normalized or any(part == ".." for part in normalized.split("/")):
            raise AttachmentInputError("El nombre del archivo no es válido")
        safe = re.sub(r"[^A-Za-z0-9._ -]", "_", normalized).strip(" .")
        if not safe:
            raise AttachmentInputError("El nombre del archivo no es válido")
        stem, extension = PurePath(safe).stem, PurePath(safe).suffix
        if not stem or stem.upper() in _RESERVED_NAMES:
            raise AttachmentInputError("El nombre del archivo no es válido")
        return f"{stem[: 255 - len(extension)]}{extension}"

    def _declared_mime(self, content_type: str | None) -> str:
        declared = (content_type or "").split(";", 1)[0].strip().casefold()
        if not declared:
            raise AttachmentMimeError("El MIME del archivo es obligatorio")
        return declared

    def _validate_declared_type(self, extension: str, declared_mime: str) -> None:
        if extension not in self.allowed_extensions:
            raise AttachmentMimeError("La extensión del archivo no está permitida")
        if declared_mime not in self.allowed_mime_types:
            raise AttachmentMimeError("El MIME del archivo no está permitido")
        if _EXTENSION_TO_MIME.get(extension) != declared_mime:
            raise AttachmentMimeError("La extensión no coincide con el MIME declarado")

    def _history(
        self,
        session: Session,
        ticket_id: str,
        actor: AuthenticatedUser,
        action: str,
        attachment_id: str,
    ) -> None:
        self.repository.add_history(
            session,
            TicketHistory(
                ticket_id=ticket_id,
                actor_id=actor.user.id,
                action=action,
                old_value=None if action == "ATTACHMENT_UPLOADED" else attachment_id,
                new_value=attachment_id if action == "ATTACHMENT_UPLOADED" else None,
                description=(
                    "Adjunto agregado al ticket"
                    if action == "ATTACHMENT_UPLOADED"
                    else "Adjunto eliminado del ticket"
                ),
            ),
        )
        self.audit.record(
            session,
            event_type="ATTACHMENT",
            action=action,
            actor_user_id=actor.user.id,
            actor_role=actor.role,
            resource_type="ATTACHMENT",
            resource_id=attachment_id,
            target_user_id=actor.user.id,
            success=True,
            metadata={"ticket_id": ticket_id},
        )
