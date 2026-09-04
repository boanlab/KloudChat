"""Admin-editable runtime settings. A row overrides the environment variable of
the same key; secrets are stored encrypted (`services/settings.py`)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class SystemSetting(SQLModel, table=True):
    __tablename__ = "system_settings"

    key: str = Field(primary_key=True)
    value: str = Field(default="")
    #: Encrypted at rest and never serialised back to the browser.
    secret: bool = Field(default=False)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_by: str | None = Field(default=None)


__all__ = ["SystemSetting"]
