"""Admin-editable settings; row overrides same-key env var, secrets encrypted."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class SystemSetting(SQLModel, table=True):
    __tablename__ = "system_settings"

    key: str = Field(primary_key=True)
    value: str = Field(default="")
    #: encrypted at rest, never sent to the browser
    secret: bool = Field(default=False)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_by: str | None = Field(default=None)


__all__ = ["SystemSetting"]
