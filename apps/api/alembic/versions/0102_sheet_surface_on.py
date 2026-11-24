"""The sheet surface is on by default; an explicit list of surfaces gains `sheet`.

`kinds.enabled` holds the administrator's saved list. A deployment that saved one before the sheet
surface existed would never show it, so `sheet` is appended once. A deployment with no saved list
follows the code default, which now includes `sheet`. An empty list (all optional surfaces off) is
left alone.

Revision ID: 0102
Revises: 0101
Create Date: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("SELECT value FROM system_settings WHERE key = 'kinds.enabled'")).first()
    if row is None or not (row[0] or "").strip():
        return
    kinds = [k.strip() for k in row[0].split(",") if k.strip()]
    if "sheet" in kinds:
        return
    bind.execute(
        sa.text("UPDATE system_settings SET value = :v WHERE key = 'kinds.enabled'"),
        {"v": ",".join([*kinds, "sheet"])},
    )


def downgrade() -> None:
    pass
