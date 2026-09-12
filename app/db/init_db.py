from sqlmodel import SQLModel

from app.db import models  # noqa: F401
from app.db.session import engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
