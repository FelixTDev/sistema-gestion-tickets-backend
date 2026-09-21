import re

from app.core.config import get_settings


def validate_password_policy(value: str) -> str:
    settings = get_settings()
    if len(value) < settings.password_min_length:
        raise ValueError("La contraseña no cumple la longitud mínima configurada")
    if settings.password_require_uppercase and not re.search(r"[A-Z]", value):
        raise ValueError("La contraseña debe incluir una mayúscula")
    if settings.password_require_lowercase and not re.search(r"[a-z]", value):
        raise ValueError("La contraseña debe incluir una minúscula")
    if settings.password_require_digit and not re.search(r"\d", value):
        raise ValueError("La contraseña debe incluir un número")
    return value
