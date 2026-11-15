"""Create a new isolated SQLite database for the chatbot demo."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def configure_runtime() -> Path:
    raw_path = os.environ.get("CHATBOT_DEMO_DB_PATH")
    if not raw_path:
        raise SystemExit("CHATBOT_DEMO_DB_PATH debe apuntar a un archivo SQLite nuevo.")
    database_path = Path(raw_path).resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        database_path.relative_to(temporary_root)
    except ValueError as error:
        raise SystemExit(
            "El seed de demo solo permite archivos dentro de TEMP."
        ) from error
    if database_path.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise SystemExit("La ruta de demo debe tener extensión SQLite.")
    if database_path.exists():
        raise SystemExit("La base ya existe; se rechaza sobrescribirla.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if (database_path.parent / ".env").exists():
        raise SystemExit("El directorio temporal debe estar libre de .env.")

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
        }
    )
    backend_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend_root))
    return database_path


def main() -> None:
    configure_runtime()

    from sqlmodel import Session, select

    from app.db.init_db import create_db_and_tables
    from app.db.session import engine
    from app.modules.tickets.models.ticket import Ticket
    from app.modules.usuarios.models.user import User
    from app.seed.chatbot_demo_data import seed_chatbot_demo_data

    create_db_and_tables()
    with Session(engine) as session:
        seed_chatbot_demo_data(session)
        ticket_count = len(session.exec(select(Ticket)).all())
        user_count = len(session.exec(select(User)).all())

    print(
        f"SQLite de demo creada: {ticket_count} tickets y {user_count} usuarios. "
        "La IA y los proveedores externos están desactivados."
    )


if __name__ == "__main__":
    main()
