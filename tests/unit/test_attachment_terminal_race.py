from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.api.deps import AuthenticatedUser
from app.modules.adjuntos.services.attachment_service import AttachmentService
from app.modules.adjuntos.services.storage_provider import StoredObject
from app.modules.tickets.models.ticket import Ticket, TicketStatus
from app.modules.usuarios.models.user import User
from app.shared.exceptions import AppError


class RaceRepository:
    def __init__(self, open_ticket: Ticket, terminal_ticket: Ticket) -> None:
        self.open_ticket = open_ticket
        self.terminal_ticket = terminal_ticket
        self.lock_called = False

    def get_ticket(self, _session, _ticket_id: str) -> Ticket:
        return self.open_ticket

    def get_ticket_for_update(self, _session, _ticket_id: str) -> Ticket:
        self.lock_called = True
        return self.terminal_ticket

    def get_comment(self, _session, _comment_id: str):
        return None

    def count_active_for_ticket(self, _session, _ticket_id: str) -> int:
        return 0

    def total_size_active_for_ticket(self, _session, _ticket_id: str) -> int:
        return 0

    def add(self, _session, _attachment):
        raise AssertionError("No debe persistir tras detectar el estado terminal")


class RaceStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def store(self, source, storage_key: str, _max_size: int) -> StoredObject:
        payload = source.read()
        return StoredObject(
            storage_key=storage_key,
            file_size=len(payload),
            sha256="0" * 64,
            detected_mime="application/pdf",
        )

    def delete(self, storage_key: str) -> None:
        self.deleted.append(storage_key)


class FakeSession:
    def rollback(self) -> None:
        return None


def test_upload_revalidates_terminal_state_under_lock_and_cleans_stored_file():
    client_id = "client-1"
    ticket_values = {
        "id": "ticket-1",
        "client_id": client_id,
        "category_id": "category-1",
        "subject": "Carrera de adjunto",
        "description": "El ticket se cierra mientras se almacena el archivo.",
    }
    repository = RaceRepository(
        Ticket(**ticket_values, status=TicketStatus.NUEVO),
        Ticket(**ticket_values, status=TicketStatus.CANCELADO),
    )
    storage = RaceStorage()
    service = AttachmentService(
        repository=repository,  # type: ignore[arg-type]
        storage_provider=storage,  # type: ignore[arg-type]
        max_file_size=1024,
        max_total_size_per_ticket=2048,
        max_files_per_ticket=2,
    )
    actor = AuthenticatedUser(
        user=User(
            id=client_id,
            role_id="role-1",
            full_name="Cliente prueba",
            email="client@example.com",
            password_hash="hash",
        ),
        role="CLIENTE",
    )
    upload = UploadFile(
        filename="evidence.pdf",
        file=BytesIO(b"%PDF-1.7\n"),
        headers=Headers({"content-type": "application/pdf"}),
    )

    with pytest.raises(AppError) as raised:
        service.upload(FakeSession(), "ticket-1", actor, upload)  # type: ignore[arg-type]

    assert raised.value.code == "TICKET_TERMINAL_MUTATION_FORBIDDEN"
    assert repository.lock_called is True
    assert len(storage.deleted) == 1
