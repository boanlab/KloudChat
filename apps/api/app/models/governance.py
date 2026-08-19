"""Instance-wide policy. One row, `id = 'default'`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
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
    #: Strict-local model ids. Every use is revalidated and prioritized in the
    #: live catalogue's visible order, so stale ids cannot become an external
    #: route and hidden click order cannot change routing.
    privacy_safe_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: Optional, explicit cost optimisation. Existing and new installations
    #: remain manual until an administrator finishes and enables the policy.
    adaptive_routing_enabled: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: Must resolve to a live, zero-priced strict-local chat model at runtime.
    adaptive_classifier_model_id: str | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    #: Ordered economy candidates. Runtime validation is repeated for every
    #: turn because catalogue price, boundary and account permissions can move.
    adaptive_economy_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
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
