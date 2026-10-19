"""Deck body size measured from 18 pt: stored `textScale` rewritten so every slide keeps its size.

`textScale` was a multiple of a 22 pt body; it is now a multiple of 18 pt (1.0 = 18 pt, 22 pt is
1.2222). A body slide with no scale was drawn at 22 pt and gets 1.2222; a scale `s` becomes
`s × 22 / 18`. Cover, divider and closing slides carry no body scale and are left alone.
Downgrade divides back.

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-18
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: slides with no body size of their own
_NO_BODY = ("title", "section", "closing")
_RATIO = 22 / 18


def _rescale(data: dict, ratio: float) -> bool:
    changed = False
    for slide in data.get("slides") or []:
        if not isinstance(slide, dict) or slide.get("layout") in _NO_BODY:
            continue
        old = float(slide.get("textScale") or 1.0)
        new = round(old * ratio, 4)
        if abs(new - 1.0) < 1e-3:
            slide.pop("textScale", None)
        else:
            slide["textScale"] = new
        changed = True
    return changed


_ARTIFACTS = sa.table(
    "artifacts", sa.column("id", sa.String), sa.column("kind", sa.String),
    sa.column("data", sa.JSON),
)


def _rewrite(ratio: float) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_ARTIFACTS.c.id, _ARTIFACTS.c.data).where(sa.cast(_ARTIFACTS.c.kind, sa.String) == "deck")
    ).all()
    for row_id, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict) and _rescale(data, ratio):
            bind.execute(
                _ARTIFACTS.update().where(_ARTIFACTS.c.id == row_id).values(data=data)
            )


def upgrade() -> None:
    _rewrite(_RATIO)


def downgrade() -> None:
    _rewrite(1 / _RATIO)
