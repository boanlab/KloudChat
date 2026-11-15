"""Super Agent is always offered and is the default start of chat, report and slides.

- `governance.super_agent_enabled` is dropped: the picker always lists both Super Agent
  entries (owner decision 2026-11-14). The routing table and allowed models still bound them.
- `system_settings` `routing.chat`, `routing.report`, `routing.slides`: how a new conversation
  of that surface starts (`super`, `super_strict`, `manual`, or "" for report and slides to
  follow chat). Unset means `super`, so nothing is written for a surface whose stored model is
  still the shipped default (`local/qwen3.8-flash` for chat, `local/qwen3.8-27b` for report
  and slides) or that no administrator saved: those move to Super Agent. A surface an
  administrator saved (audit `settings.update` with that field) to something else keeps its
  fixed model (`manual`, or "" when report or slides were set to follow chat). The stored
  models themselves are left as they are; they stay the fallback.
- Existing conversations keep their stored mode and model.

Downgrade removes the three rows and restores the column (on).

Revision ID: 0089
Revises: 0088
Create Date: 2026-11-14
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: stored model key → (start key, audited settings field, shipped default). Frozen here.
SURFACES: dict[str, tuple[str, str, str]] = {
    "models.chat": ("routing.chat", "model_chat", "local/qwen3.8-flash"),
    "models.report": ("routing.report", "model_report", "local/qwen3.8-27b"),
    "models.slides": ("routing.slides", "model_slides", "local/qwen3.8-27b"),
}


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def admin_saved(conn) -> set[str]:
    """Settings fields an administrator's save changed (audit `settings.update`)."""
    saved: set[str] = set()
    rows = conn.execute(
        sa.text("SELECT metadata FROM audit_events WHERE action = 'settings.update'")
    ).all()
    for (metadata,) in rows:
        changes = (_json(metadata) or {}).get("changes") or {}
        if isinstance(changes, dict):
            saved.update(str(field) for field in changes)
    return saved


def kept_starts(stored: dict[str, str], saved: set[str]) -> dict[str, str]:
    """Start rows to write: only surfaces an administrator moved off the shipped default."""
    out: dict[str, str] = {}
    for model_key, (start_key, field, shipped) in SURFACES.items():
        if start_key in stored or model_key not in stored or field not in saved:
            continue
        value = (stored[model_key] or "").strip()
        if value == shipped:
            continue
        out[start_key] = "" if value == "" and model_key != "models.chat" else "manual"
    return out


def upgrade() -> None:
    conn = op.get_bind()
    stored = dict(conn.execute(sa.text("SELECT key, value FROM system_settings")).all())
    now = datetime.now(UTC)
    for key, value in kept_starts(stored, admin_saved(conn)).items():
        conn.execute(
            sa.text(
                "INSERT INTO system_settings (key, value, secret, updated_at, updated_by) "
                "VALUES (:key, :value, :secret, :now, NULL)"
            ),
            {"key": key, "value": value, "secret": False, "now": now},
        )
    op.drop_column("governance", "super_agent_enabled")


def downgrade() -> None:
    op.add_column(
        "governance",
        sa.Column("super_agent_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    conn = op.get_bind()
    for start_key, _field, _shipped in SURFACES.values():
        conn.execute(
            sa.text("DELETE FROM system_settings WHERE key = :key"), {"key": start_key}
        )
