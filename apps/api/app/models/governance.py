"""Instance-wide policy; single row `id = 'default'`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.config import settings
from app.models.user import utcnow


def _ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class Governance(SQLModel, table=True):
    __tablename__ = "governance"

    id: str = Field(default="default", primary_key=True)
    #: PII redaction (RRN, phone, card, email) before any model
    pii_masking: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: full-envelope check before external/hybrid models
    external_data_guard: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
    #: upper bound: raw external delivery only if admin permits
    allow_user_raw_external: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    #: strict-local ids, revalidated against live catalogue
    privacy_safe_model_ids: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    adaptive_routing_enabled: bool = Field(
        default_factory=lambda: settings.adaptive_routing_default,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    #: must resolve to a live, zero-priced strict-local model
    adaptive_classifier_model_id: str | None = Field(
        default_factory=lambda: settings.adaptive_classifier_model_default or None,
        sa_column=Column(String, nullable=True),
    )
    #: ordered, revalidated every turn
    adaptive_economy_model_ids: list = Field(
        default_factory=lambda: _ids(settings.adaptive_economy_models_default),
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: independent of `adaptive_routing_enabled`
    adaptive_quality_enabled: bool = Field(
        default_factory=lambda: settings.adaptive_quality_default,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    #: ordered, admin-curated (not price-sorted), revalidated every turn
    adaptive_quality_model_ids: list = Field(
        default_factory=lambda: _ids(settings.adaptive_quality_models_default),
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: internal material forces strict-local: `strict-local/` twin, else first privacy-safe model
    internal_data_strict_local: bool = Field(
        default_factory=lambda: settings.internal_data_strict_local_default,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    #: outline call only; empty: surface model
    outline_model_id: str | None = Field(
        default_factory=lambda: settings.outline_model_default or None,
        sa_column=Column(String, nullable=True),
    )
    intent_filter: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    blocked_categories: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: minutes; 0 disables. Browser-enforced (only it knows idleness).
    idle_timeout_minutes: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    #: instance-wide published-bot turn cap; overflow queues, then 429
    published_concurrency_max: int = Field(
        default=16, sa_column=Column(Integer, nullable=False, server_default="16")
    )
    retention_days: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    updated_by: str | None = Field(default=None)


__all__ = ["Governance"]
