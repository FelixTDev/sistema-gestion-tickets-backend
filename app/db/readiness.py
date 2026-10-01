from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session


class DatabaseNotReadyError(Exception):
    """Raised when the configured database cannot answer a bounded probe."""


def ensure_database_ready(session: Session) -> None:
    try:
        session.exec(text("SELECT /*+ MAX_EXECUTION_TIME(1000) */ 1")).one()
    except (SQLAlchemyError, TimeoutError, OSError) as error:
        raise DatabaseNotReadyError from error
