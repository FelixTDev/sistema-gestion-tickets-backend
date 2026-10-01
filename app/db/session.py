from collections.abc import Generator

from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from app.core.config import Settings, get_settings


def database_connect_args(settings: Settings) -> dict[str, int]:
    if make_url(settings.database_url).drivername != "mysql+pymysql":
        return {}
    timeout = settings.database_connect_timeout_seconds
    return {
        "connect_timeout": timeout,
        "read_timeout": timeout,
        "write_timeout": timeout,
    }


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=database_connect_args(settings),
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
