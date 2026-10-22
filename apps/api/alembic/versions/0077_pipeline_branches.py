"""Branching user pipelines: stable step ids and edges between steps.

- `user_pipelines.edges`: `[{from, to, branch?}]` by step id; null keeps the steps running in
  their stored order (every pipeline so far).
- Steps stored without an id get the one they were already shown under (`s1`, `s2` … by
  position, a repeat suffixed `x`), so order and run records stay as they were.

Downgrade drops the column; the ids stay (an id was always accepted).

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-20
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PIPELINES = sa.table("user_pipelines", sa.column("id", sa.String),
                      sa.column("stages", sa.JSON))


def with_ids(stages: list) -> bool:
    """Ids given in place as `compose.normalize` reads them; whether any was missing."""
    seen: set[str] = set()
    changed = False
    for i, row in enumerate(stages):
        if not isinstance(row, dict):
            continue
        sid = re.sub(r"[^A-Za-z0-9_-]", "", str(row.get("id") or ""))[:40] or f"s{i + 1}"
        while sid in seen:
            sid = f"{sid}x"
        seen.add(sid)
        if row.get("id") != sid:
            row["id"] = sid
            changed = True
    return changed


def upgrade() -> None:
    json_type = postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()
    op.add_column("user_pipelines", sa.Column("edges", json_type, nullable=True))
    bind = op.get_bind()
    for row_id, stages in bind.execute(sa.select(_PIPELINES.c.id, _PIPELINES.c.stages)).all():
        if isinstance(stages, str):
            stages = json.loads(stages)
        if isinstance(stages, list) and with_ids(stages):
            bind.execute(_PIPELINES.update().where(_PIPELINES.c.id == row_id)
                         .values(stages=stages))


def downgrade() -> None:
    op.drop_column("user_pipelines", "edges")
