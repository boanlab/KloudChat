"""Instance-wide policy. One row, `id = 'default'`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class Governance(SQLModel, table=True):
    __tablename__ = "governance"

    id: str = Field(default="default", primary_key=True)
    #: Redact resident registration numbers, phone numbers, card numbers and
    #: emails from anything on its way to a model.
    pii_masking: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Refuse prompts whose intent falls in `blocked_categories`.
    intent_filter: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    blocked_categories: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Delete message bodies older than this. 0 keeps everything.
    retention_days: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_by: str | None = Field(default=None)


__all__ = ["Governance"]
