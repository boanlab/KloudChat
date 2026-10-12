"""Shipped skills are the administrator's own skills, shared; no `built-in` rows remain.

- `skills.package_hash`: the owner row's content as last synced from its package. The
  startup sync follows the package while the hash still matches and leaves an edited row
  alone; copies equal to the owner row's previous content follow with it.
- `source = built_in` is rewritten: the catalogue owner's (oldest admin's) keyed rows become
  `personal` (authored by that admin), everyone else's become `workspace` (copies of a shared
  skill). The owner's unkeyed `built_in` rows (per-account seeds of old) become `workspace`
  with no origin, which the sync adopts by name as before.
- Copies seeded before `origin_id` are linked to the owner row by key on the next sync.

Downgrade drops the column and marks keyed rows `built_in` again.

Revision ID: 0070
Revises: 0068
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0070"
# re-parent onto the head current at merge time (0069 lands separately)
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER = ("(SELECT id FROM users WHERE role = 'admin' "
          "ORDER BY created_at, id LIMIT 1)")


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("package_hash", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
    )
    op.execute(f"""
        UPDATE skills SET source = 'personal'
        WHERE source = 'built_in' AND catalog_key IS NOT NULL AND owner_id = {_OWNER}
    """)
    op.execute("UPDATE skills SET source = 'workspace' WHERE source = 'built_in'")


def downgrade() -> None:
    op.execute("""
        UPDATE skills SET source = 'built_in'
        WHERE catalog_key IS NOT NULL AND source IN ('personal', 'workspace')
    """)
    op.drop_column("skills", "package_hash")
