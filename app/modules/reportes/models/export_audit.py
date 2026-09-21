from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, Integer, String
from sqlmodel import Field, SQLModel

from app.modules.usuarios.models.user import utc_now


class ReportExportAudit(SQLModel, table=True):
    __tablename__ = "report_export_audits"
    __table_args__ = (
        Index("ix_report_export_audits_actor_created", "actor_id", "created_at"),
        Index("ix_report_export_audits_report_created", "report_name", "created_at"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()), primary_key=True, max_length=36
    )
    actor_id: str = Field(foreign_key="users.id", max_length=36)
    report_name: str = Field(sa_column=Column(String(64), nullable=False))
    export_format: str = Field(sa_column=Column(String(10), nullable=False))
    filters_json: dict[str, object] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    row_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    succeeded: bool = Field(sa_column=Column(Boolean, nullable=False))
    error_code: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
