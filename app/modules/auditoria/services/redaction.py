from collections.abc import Mapping
from typing import Any

_REDACTED = "[REDACTED]"
_BINARY_REDACTED = "[BINARY_REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_MAX_STRING_LENGTH = 512
_MAX_DEPTH = 6
_MAX_ITEMS = 100
_SENSITIVE_PARTS = {
    "password",
    "passwordhash",
    "token",
    "accesstoken",
    "refreshtoken",
    "secret",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "hash",
    "privatekey",
    "clientsecret",
    "email",
}


def _normalized_key(key: object) -> str:
    return "".join(char for char in str(key).casefold() if char.isalnum())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact_data(value: Any, *, _depth: int = 0) -> Any:
    """Produce una estructura JSON segura, determinista y de tamaño acotado."""
    if _depth >= _MAX_DEPTH:
        return _TRUNCATED
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _BINARY_REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED
            if _is_sensitive_key(key)
            else redact_data(item, _depth=_depth + 1)
            for key, item in list(value.items())[:_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            redact_data(item, _depth=_depth + 1) for item in list(value)[:_MAX_ITEMS]
        ]
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            return f"{value[:_MAX_STRING_LENGTH]}{_TRUNCATED}"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_STRING_LENGTH]
