"""Generate the local OpenAPI contract without reading .env or connecting to DB."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if (Path.cwd() / ".env").exists():
        raise SystemExit("Ejecuta desde un directorio temporal sin .env.")

    backend_root = Path(__file__).resolve().parents[2]
    os.environ.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": "sqlite://",
            "CHATBOT_AI_ENABLED": "false",
            "CHATBOT_AI_PROVIDER": "noop",
            "CHATBOT_AI_API_KEY": "",
            "CHATBOT_AI_ENDPOINT": "",
            "OPENAI_API_KEY": "",
            "EMAIL_PROVIDER": "noop",
        }
    )
    sys.path.insert(0, str(backend_root))

    from app.main import app

    schema = app.openapi()
    components = schema["components"]["schemas"]
    intents = components["ChatbotIntent"]["enum"]
    ui_types = components["ChatbotUIType"]["enum"]
    paths = schema["paths"]
    print(
        "OpenAPI",
        schema["openapi"],
        "paths",
        len(paths),
        "chat_paths",
        sum(path.startswith("/api/v1/chat/") for path in paths),
    )
    print(
        "FAQ_SUBCATEGORY_DISCOVERY",
        "FAQ_SUBCATEGORY_CHOICES" in ui_types,
        "FAQ_SUBCATEGORY_DISCOVERY" in intents,
    )


if __name__ == "__main__":
    main()
