"""Instance-wide policy. One row, `id = 'default'`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.config import settings
from app.models.user import utcnow


class Governance(SQLModel, table=True):
    __tablename__ = "governance"

    id: str = Field(default="default", primary_key=True)
    #: Redact resident registration numbers, phone numbers, card numbers and
    #: emails from anything on its way to a model.
    pii_masking: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Inspect the complete chat envelope before it is sent to an external or
    #: hybrid model.
    external_data_guard: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
    #: An upper bound, not a default. Users cannot choose raw external delivery
    #: unless the administrator explicitly permits it.
    allow_user_raw_external: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Strict-local model ids, revalidated against the live catalogue on every use.
    privacy_safe_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Cost-saving auto routing. Off until an administrator enables it.
    adaptive_routing_enabled: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Must resolve to a live, zero-priced strict-local chat model at runtime.
    adaptive_classifier_model_id: str | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    #: Ordered economy candidates, revalidated every turn.
    adaptive_economy_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Quality-upgrade auto routing; independent of `adaptive_routing_enabled`.
    adaptive_quality_enabled: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Ordered upgrade candidates, revalidated every turn. Administrator-curated,
    #: not a price sort.
    adaptive_quality_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: A turn that carries the organisation's own material — an attachment, a project's
    #: files or instructions, a memory — runs on a strict-local model whatever was chosen:
    #: the `strict-local/` twin of a `local/` model, else the first privacy-safe model.
    internal_data_strict_local: bool = Field(
        default_factory=lambda: settings.internal_data_strict_local_default,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    #: Model for a document's outline call only; the blocks use the surface's
    #: own model. Empty keeps the surface's model for the outline too.
    outline_model_id: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    #: Refuse prompts whose intent falls in `blocked_categories`.
    intent_filter: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    blocked_categories: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Sign an idle browser out after this many minutes; 0 disables. Enforced
    #: by the browser, which alone knows idleness (the silent refresh is a timer).
    idle_timeout_minutes: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
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
