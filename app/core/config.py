from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ticket Management API"
    app_env: str = "development"
    debug: bool = False
    database_url: str = "mysql+pymysql://tickets:tickets@localhost:3306/tickets"
    secret_key: str = "change-this-development-secret-key-32-chars-min"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    password_min_length: int = 8
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_digit: bool = True
    password_reset_token_expire_minutes: int = 30
    email_verification_token_expire_hours: int = 24
    auth_rate_limit_max_attempts: int = 5
    auth_rate_limit_window_seconds: int = 900
    auth_rate_limit_block_seconds: int = 900
    attachment_storage_dir: str = "var/uploads"
    attachment_max_file_size_bytes: int = 10 * 1024 * 1024
    attachment_max_total_size_per_ticket_bytes: int = 50 * 1024 * 1024
    attachment_max_files_per_ticket: int = 10
    attachment_allowed_extensions: list[str] = [
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".txt",
    ]
    attachment_allowed_mime_types: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "text/plain",
    ]
    sla_default_timezone: str = "UTC"
    sla_default_calendar: str = "24x7"
    sla_policy_defaults: dict[str, dict[str, int]] = {
        "BAJA": {
            "first_response_seconds": 86_400,
            "resolution_seconds": 259_200,
            "warning_seconds": 21_600,
        },
        "MEDIA": {
            "first_response_seconds": 43_200,
            "resolution_seconds": 172_800,
            "warning_seconds": 10_800,
        },
        "ALTA": {
            "first_response_seconds": 14_400,
            "resolution_seconds": 86_400,
            "warning_seconds": 7_200,
        },
        "URGENTE": {
            "first_response_seconds": 3_600,
            "resolution_seconds": 28_800,
            "warning_seconds": 1_800,
        },
    }
    report_export_max_rows: int = 10_000
    report_export_max_range_days: int = 366
    chatbot_high_confidence_threshold: float = 0.70
    chatbot_medium_confidence_threshold: float = 0.35
    chatbot_max_turns: int = 50
    chatbot_messages_per_minute: int = 20
    chatbot_max_message_length: int = 2000
    chatbot_max_anonymous_conversations_per_hour: int = 10
    chatbot_max_clarification_attempts: int = 2
    chatbot_max_conversions_per_day: int = 5
    chatbot_ai_enabled: bool = False
    chatbot_ai_provider: str = "noop"
    chatbot_ai_endpoint: str | None = None
    chatbot_ai_api_key: str | None = None
    chatbot_ai_model: str = "disabled"
    chatbot_ai_prompt_version: str = "v1"
    chatbot_ai_timeout_seconds: float = 3.0
    chatbot_ai_max_output_chars: int = 1200
    chatbot_ai_max_documents: int = 3
    chatbot_ai_max_context_chars: int = 6000
    chatbot_ai_min_confidence: float = 0.70
    chatbot_ai_requests_per_minute: int = 5
    chatbot_ai_max_responses_per_conversation: int = 3
    chatbot_ai_max_consecutive_failures: int = 3
    chatbot_ai_cooldown_seconds: int = 60
    chatbot_ai_idempotency_window_seconds: int = 30
    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        if self.jwt_algorithm != "HS256":
            raise ValueError("Solo se permite el algoritmo JWT HS256")
        if self.password_min_length < 8:
            raise ValueError("La longitud mínima de contraseña debe ser 8")
        if self.access_token_expire_minutes < 1:
            raise ValueError("La expiración del token de acceso debe ser positiva")
        if self.password_reset_token_expire_minutes < 1:
            raise ValueError(
                "La expiración del token de recuperación debe ser positiva"
            )
        if self.email_verification_token_expire_hours < 1:
            raise ValueError(
                "La expiración del token de verificación debe ser positiva"
            )
        if self.auth_rate_limit_max_attempts < 1:
            raise ValueError("El límite de intentos debe ser positivo")
        if self.auth_rate_limit_window_seconds < 1:
            raise ValueError("La ventana de rate limit debe ser positiva")
        if self.auth_rate_limit_block_seconds < 1:
            raise ValueError("El bloqueo de rate limit debe ser positivo")
        if self.attachment_max_file_size_bytes < 1:
            raise ValueError("El tamaño máximo de adjunto debe ser positivo")
        if self.attachment_max_total_size_per_ticket_bytes < 1:
            raise ValueError("La cuota total de adjuntos debe ser positiva")
        if self.attachment_max_files_per_ticket < 1:
            raise ValueError("La cantidad máxima de adjuntos debe ser positiva")
        if self.report_export_max_rows < 1:
            raise ValueError("El máximo de filas de exportación debe ser positivo")
        if self.report_export_max_range_days < 1:
            raise ValueError("El rango máximo de reportes debe ser positivo")
        if not 0 < self.chatbot_medium_confidence_threshold < 1:
            raise ValueError("El umbral medio del chatbot debe estar entre 0 y 1")
        if not (
            self.chatbot_medium_confidence_threshold
            < self.chatbot_high_confidence_threshold
            <= 1
        ):
            raise ValueError("Los umbrales de confianza del chatbot no son válidos")
        if self.chatbot_max_turns < 1:
            raise ValueError("El máximo de turnos del chatbot debe ser positivo")
        if self.chatbot_messages_per_minute < 1:
            raise ValueError("El límite de mensajes del chatbot debe ser positivo")
        if not 1 <= self.chatbot_max_message_length <= 10_000:
            raise ValueError(
                "La longitud máxima del mensaje del chatbot debe estar entre 1 y 10000"
            )
        if self.chatbot_max_anonymous_conversations_per_hour < 1:
            raise ValueError("El límite de conversaciones anónimas debe ser positivo")
        if self.chatbot_max_clarification_attempts < 1:
            raise ValueError("El límite de aclaraciones del chatbot debe ser positivo")
        if self.chatbot_max_conversions_per_day < 1:
            raise ValueError("El límite de conversiones del chatbot debe ser positivo")
        if not self.chatbot_ai_provider.strip():
            raise ValueError("El proveedor IA no puede estar vacío")
        if not self.chatbot_ai_model.strip():
            raise ValueError("El modelo IA no puede estar vacío")
        if not self.chatbot_ai_prompt_version.strip():
            raise ValueError("La versión de prompt IA no puede estar vacía")
        if not 0.1 <= self.chatbot_ai_timeout_seconds <= 30:
            raise ValueError("El timeout IA debe estar entre 0.1 y 30 segundos")
        if not 1 <= self.chatbot_ai_max_output_chars <= 10_000:
            raise ValueError("La salida máxima IA debe estar entre 1 y 10000")
        if not 1 <= self.chatbot_ai_max_documents <= 10:
            raise ValueError("La cantidad de documentos IA debe estar entre 1 y 10")
        if not 100 <= self.chatbot_ai_max_context_chars <= 50_000:
            raise ValueError("El contexto máximo IA no es válido")
        if not 0 < self.chatbot_ai_min_confidence <= 1:
            raise ValueError("La confianza mínima IA debe estar entre 0 y 1")
        if self.chatbot_ai_requests_per_minute < 1:
            raise ValueError("El límite de solicitudes IA debe ser positivo")
        if self.chatbot_ai_max_responses_per_conversation < 1:
            raise ValueError("El límite de respuestas IA debe ser positivo")
        if self.chatbot_ai_max_consecutive_failures < 1:
            raise ValueError("El límite de fallos IA debe ser positivo")
        if self.chatbot_ai_cooldown_seconds < 1:
            raise ValueError("El cooldown IA debe ser positivo")
        if self.chatbot_ai_idempotency_window_seconds < 1:
            raise ValueError("La ventana de idempotencia IA debe ser positiva")
        self.attachment_allowed_extensions = [
            extension.casefold()
            if extension.startswith(".")
            else f".{extension.casefold()}"
            for extension in self.attachment_allowed_extensions
        ]
        self.attachment_allowed_mime_types = [
            mime.casefold() for mime in self.attachment_allowed_mime_types
        ]
        if (
            not self.attachment_allowed_extensions
            or not self.attachment_allowed_mime_types
        ):
            raise ValueError("Las listas de adjuntos permitidos no pueden estar vacías")
        if self.sla_default_timezone != "UTC":
            raise ValueError("La zona horaria SLA local debe ser UTC")
        if self.sla_default_calendar != "24x7":
            raise ValueError("El calendario SLA local debe ser 24x7")
        required_sla_fields = {
            "first_response_seconds",
            "resolution_seconds",
            "warning_seconds",
        }
        if not self.sla_policy_defaults:
            raise ValueError("Debe existir al menos una política SLA por defecto")
        for priority, policy in self.sla_policy_defaults.items():
            if not priority or set(policy) != required_sla_fields:
                raise ValueError("La configuración de políticas SLA no es válida")
            if any(value < 0 for value in policy.values()) or any(
                policy[field] < 1
                for field in ("first_response_seconds", "resolution_seconds")
            ):
                raise ValueError("Los tiempos SLA deben ser positivos")
        if self.app_env.casefold() in {"production", "staging"} and (
            len(self.secret_key) < 32 or self.secret_key.startswith("change-this")
        ):
            raise ValueError("SECRET_KEY segura requerida fuera de desarrollo")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
