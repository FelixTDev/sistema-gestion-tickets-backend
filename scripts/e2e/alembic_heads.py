"""Run Alembic's heads command from a .env-free working directory."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    if (Path.cwd() / ".env").exists():
        raise SystemExit("Ejecuta desde un directorio temporal sin .env.")

    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("prepend_sys_path", str(backend_root))
    config.set_main_option("sqlalchemy.url", "sqlite://")
    command.heads(config)


if __name__ == "__main__":
    main()
