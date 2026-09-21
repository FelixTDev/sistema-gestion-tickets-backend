from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import BinaryIO, Protocol


class StorageError(Exception):
    """Base error for binary storage operations."""


class StorageNotFoundError(StorageError):
    pass


class StorageTooLargeError(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    storage_key: str
    file_size: int
    sha256: str
    detected_mime: str | None


class StorageProvider(Protocol):
    def store(self, source: BinaryIO, storage_key: str, max_size: int) -> StoredObject:
        """Persist a stream under an internal key and return safe metadata."""

    def open(self, storage_key: str) -> BinaryIO:
        """Open an object without exposing the provider's physical path."""

    def delete(self, storage_key: str) -> None:
        """Delete an object by its internal key."""


def detect_mime(sample: bytes) -> str | None:
    if not sample:
        return None
    if sample.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if sample.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if sample.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "text/plain"
    return "application/octet-stream"


class LocalStorageProvider:
    """Private local storage for development; no static route exposes this root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def store(self, source: BinaryIO, storage_key: str, max_size: int) -> StoredObject:
        target = self._safe_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_path = target.parent / f".{token_hex(16)}.part"
        total = 0
        digest = sha256()
        sample = bytearray()
        try:
            with temp_path.open("wb") as destination:
                while chunk := source.read(64 * 1024):
                    total += len(chunk)
                    if total > max_size:
                        raise StorageTooLargeError
                    digest.update(chunk)
                    if len(sample) < 8192:
                        sample.extend(chunk[: 8192 - len(sample)])
                    destination.write(chunk)
                destination.flush()
            temp_path.replace(target)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return StoredObject(
            storage_key=storage_key,
            file_size=total,
            sha256=digest.hexdigest(),
            detected_mime=detect_mime(bytes(sample)),
        )

    def open(self, storage_key: str) -> BinaryIO:
        path = self._safe_path(storage_key)
        try:
            return path.open("rb")
        except FileNotFoundError as error:
            raise StorageNotFoundError from error

    def delete(self, storage_key: str) -> None:
        self._safe_path(storage_key).unlink(missing_ok=True)

    def _safe_path(self, storage_key: str) -> Path:
        relative = PurePosixPath(storage_key)
        if (
            relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or any(part in {"", "."} for part in relative.parts)
        ):
            raise StorageError("Clave de almacenamiento inválida")
        path = (self.root / Path(*relative.parts)).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("Clave de almacenamiento fuera del directorio permitido")
        return path
