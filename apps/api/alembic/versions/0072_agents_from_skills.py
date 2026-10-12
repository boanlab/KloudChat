"""Agents are built from skills; the shipped agents give way to skills (D21).

- `agents.active_skill_ids`: the skills an agent keeps on in every turn of its conversations
  (「켜 둘 스킬」 in the agent form). Null for every existing agent: nothing changes for them.

The shipped agents (회의록, 비교·결정, 개조식 보고, 자료 추출·집계, 규정·업무 안내, and the drafts
공문·기안 초안, 발표 자료) are withdrawn in their catalogue files (`withdrawn = true`), not here:
the startup catalogue sync keeps their rows and keeps them up to date as before, the agent
list and the base picker stop offering them, and installing one is refused. No row is
deleted or changed by this revision, so every copy a member made, every conversation with a
shipped agent or a copy, every agent built on one as its base, and every published bot keeps
working. What they did is shipped as admin-owned shared skills (`app/skills/`), seeded by the
same sync.

Downgrade drops the column.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("active_skill_ids", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "active_skill_ids")
