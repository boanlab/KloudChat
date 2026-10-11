"""Which model does what is decided in the admin screen; the environment only seeds it.

- `system_settings` gains one row per model role (`models.chat`, `models.report`,
  `models.slides`, `models.image`, `models.audio`, `models.video`, `models.title`,
  `models.code`, `models.code_fallback`), the two thinking switches (`thinking.self_hosted`,
  `thinking.external`) and the new-account allowance (`credits.default_monthly`), written
  from the environment this migration runs in (`DEFAULT_CHAT_MODEL`, `TITLE_MODEL`, …), so
  an install behaves as it did. A key already stored is left alone.
- The title model is the exception: a value that is one of the earlier code defaults (the
  27B and its predecessors) was never a choice, and becomes `local/qwen3.8-flash`.
- Governance: an Auto classifier still on the earlier default `strict-local/qwen3.8-27b`,
  and an outline model still on `local/qwen3.5-122b`, move to Flash unless the audit log
  shows an administrator saved that field. `qwen3.8-flash-next`, the earlier name of
  `qwen3.8-flash`, is renamed wherever a model id is stored (classifier, outline, economy
  and upgrade lists, the OCR vision model).

Downgrade removes the seeded rows (the environment applies again); the governance moves
and the rename stay.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-11
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Stored key → `Settings` field it is seeded from. Frozen here: the app's own map may move.
MODEL_KEYS: dict[str, str] = {
    "models.chat": "default_chat_model",
    "models.report": "default_report_model",
    "models.slides": "default_slides_model",
    "models.image": "default_image_model",
    "models.audio": "default_audio_model",
    "models.video": "default_video_model",
    "models.title": "title_model",
    "models.code": "code_model",
    "models.code_fallback": "code_fallback_model",
}
FLAG_KEYS: dict[str, str] = {
    "thinking.self_hosted": "self_hosted_chat_thinking",
    "thinking.external": "external_chat_thinking",
}
CREDITS_KEY = "credits.default_monthly"
SEEDED = (*MODEL_KEYS, *FLAG_KEYS, CREDITS_KEY)

FLASH = "local/qwen3.8-flash"
STRICT_FLASH = "strict-local/qwen3.8-flash"
#: Earlier code defaults of `TITLE_MODEL`.
TITLE_DEFAULTS = {
    "local/qwen3.8-27b",
    "local/qwen3.6-35b",
    "local/glm-4.7-flash",
    "local/qwen3.8-flash-next",
}
RENAMED = {
    "local/qwen3.8-flash-next": FLASH,
    "strict-local/qwen3.8-flash-next": STRICT_FLASH,
}


def _env() -> dict[str, str]:
    """The seed values from the environment this migration runs in."""
    from app.core.config import settings

    out: dict[str, str] = {}
    for key, field in MODEL_KEYS.items():
        value = str(getattr(settings, field) or "").strip()
        out[key] = RENAMED.get(value, value)
    if out["models.title"] in TITLE_DEFAULTS:
        out["models.title"] = FLASH
    for key, field in FLAG_KEYS.items():
        out[key] = "1" if getattr(settings, field) else "0"
    out[CREDITS_KEY] = str(int(settings.default_monthly_credits))
    return out


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _admin_saved(conn, field: str) -> bool:
    """Whether an administrator's governance save changed `field`."""
    rows = conn.execute(
        sa.text("SELECT metadata FROM audit_events WHERE action = 'governance.update'")
    ).all()
    for (metadata,) in rows:
        changes = (_json(metadata) or {}).get("changes") or {}
        if isinstance(changes, dict) and field in changes:
            return True
    return False


def _governance(conn) -> None:
    ids = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
    governance = sa.table(
        "governance",
        sa.column("id", sa.String()),
        sa.column("adaptive_classifier_model_id", sa.String()),
        sa.column("outline_model_id", sa.String()),
        sa.column("adaptive_economy_model_ids", ids),
        sa.column("adaptive_quality_model_ids", ids),
    )
    classifier_chosen = _admin_saved(conn, "adaptive_classifier_model_id")
    outline_chosen = _admin_saved(conn, "outline_model_id")
    for row in conn.execute(sa.select(governance)).mappings().all():
        values: dict = {}
        classifier = row["adaptive_classifier_model_id"]
        if classifier in RENAMED:
            values["adaptive_classifier_model_id"] = RENAMED[classifier]
        elif classifier == "strict-local/qwen3.8-27b" and not classifier_chosen:
            values["adaptive_classifier_model_id"] = STRICT_FLASH
        outline = row["outline_model_id"]
        if outline in RENAMED:
            values["outline_model_id"] = RENAMED[outline]
        elif outline == "local/qwen3.5-122b" and not outline_chosen:
            values["outline_model_id"] = FLASH
        for column in ("adaptive_economy_model_ids", "adaptive_quality_model_ids"):
            listed = _json(row[column]) or []
            renamed = [RENAMED.get(model_id, model_id) for model_id in listed]
            if renamed != listed:
                values[column] = renamed
        if values:
            conn.execute(governance.update().where(governance.c.id == row["id"]).values(values))


def upgrade() -> None:
    conn = op.get_bind()
    stored = dict(conn.execute(sa.text("SELECT key, value FROM system_settings")).all())
    now = datetime.now(UTC)
    for key, value in _env().items():
        if key in stored:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO system_settings (key, value, secret, updated_at, updated_by) "
                "VALUES (:key, :value, :secret, :now, NULL)"
            ),
            {"key": key, "value": value, "secret": False, "now": now},
        )
    vision = stored.get("ocr.vision_model")
    if vision in RENAMED:
        conn.execute(
            sa.text("UPDATE system_settings SET value = :value WHERE key = 'ocr.vision_model'"),
            {"value": RENAMED[vision]},
        )
    _governance(conn)


def downgrade() -> None:
    conn = op.get_bind()
    for key in SEEDED:
        conn.execute(sa.text("DELETE FROM system_settings WHERE key = :key"), {"key": key})
