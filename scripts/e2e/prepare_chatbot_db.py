"""Create a disposable SQLite database for the browser chatbot E2E flow."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def configure_runtime() -> Path:
    raw_database_path = os.environ.get("CHATBOT_E2E_DB_PATH")
    if not raw_database_path:
        raise SystemExit("CHATBOT_E2E_DB_PATH es obligatorio.")
    demo_client_password = os.environ.get("DEMO_CLIENT_PASSWORD")
    if not demo_client_password:
        raise SystemExit(
            "DEMO_CLIENT_PASSWORD es obligatorio para el usuario ficticio."
        )

    database_path = Path(raw_database_path).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        database_path.relative_to(temporary_root)
    except ValueError as error:
        raise SystemExit(
            "La base E2E debe ubicarse dentro del directorio temporal."
        ) from error

    if database_path.exists():
        raise SystemExit("La ruta de SQLite E2E ya existe; se rechaza sobrescribirla.")
    if (database_path.parent / ".env").exists():
        raise SystemExit("El directorio E2E debe estar libre de .env.")

    os.environ.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "CHATBOT_AI_ENABLED": "false",
            "CHATBOT_AI_PROVIDER": "noop",
            "CHATBOT_AI_API_KEY": "",
            "CHATBOT_AI_ENDPOINT": "",
            "OPENAI_API_KEY": "",
            "EMAIL_PROVIDER": "noop",
            "DEMO_CLIENT_PASSWORD": demo_client_password,
        }
    )

    backend_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend_root))
    return database_path


def main() -> None:
    configure_runtime()

    from sqlmodel import Session

    from app.db.init_db import create_db_and_tables
    from app.db.session import engine
    from app.seed.demo_data import seed_demo_data

    create_db_and_tables()
    with Session(engine) as session:
        seed_demo_data(session, include_operational_data=False)

    print(
        "SQLite E2E lista: catálogo FAQ público y usuario ficticio; "
        "sin tickets iniciales."
    )


if __name__ == "__main__":
    main()
