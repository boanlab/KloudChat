"""Runtime configuration an administrator can change without a redeploy.

Environment variables stay the bootstrap: they are what a fresh container starts
with, and they keep working if this table is empty. A row here overrides one — so
an operator can point the instance at a different LiteLLM, or rotate the master
key, from the admin screen instead of editing compose and restarting.

Secret values are stored encrypted (see `services/settings.py`). That protects a
database dump, which is the realistic exposure; it does not protect someone who
already has the application's environment, and it is not meant to.
"""

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
