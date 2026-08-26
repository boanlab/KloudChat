"""One @handle per owner.

`agents.slug` had no constraint, the create and update routes never looked at
what was already there, and the form's typed value was not even sent — so an
account could hold @회의록-정리 four times, and did. The routes now refuse a
duplicate before the write; this puts the same rule on the table.

Existing duplicates are kept, not deleted: the oldest row keeps its handle and
each later one gets `-2`, `-3`, … appended. Rows with an empty slug — possible
from paths that never set one — are given one from their name first, the same
way the route derives it.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-26
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _slug(name: str) -> str:
    """The route's rule, at this revision. Duplicated on purpose: a migration
    describes the database as it was, not whatever the application ships."""
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


def resolve_slugs(rows: list[tuple[str, str, str, str]]) -> list[tuple[str, str]]:
    """`(id, owner_id, name, slug)` in the order they were created →
    `(id, new_slug)` for every row whose slug has to change.

    Pure, so it can be tested without a database. First come, first kept.
    """
    taken: dict[str, set[str]] = {}
    changes: list[tuple[str, str]] = []
    for row_id, owner_id, name, slug in rows:
        wanted = slug or _slug(name)
        seen = taken.setdefault(owner_id, set())
        candidate = wanted
        n = 2
        while candidate in seen:
            suffix = f"-{n}"
            candidate = wanted[: 60 - len(suffix)] + suffix
            n += 1
        seen.add(candidate)
        if candidate != slug:
            changes.append((row_id, candidate))
    return changes


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, owner_id, name, slug FROM agents ORDER BY owner_id, created_at, id"
        )
    ).fetchall()
    for row_id, new_slug in resolve_slugs([tuple(r) for r in rows]):
        bind.execute(
            sa.text("UPDATE agents SET slug = :slug WHERE id = :id"),
            {"slug": new_slug, "id": row_id},
        )
    op.create_unique_constraint("ux_agents_owner_slug", "agents", ["owner_id", "slug"])


def downgrade() -> None:
    # The suffixes stay: they are valid handles and removing them would
    # recreate the collisions this revision exists to end.
    op.drop_constraint("ux_agents_owner_slug", "agents", type_="unique")
