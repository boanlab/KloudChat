"""A catalogue entry can be provided to everyone: `connector_catalog.by_default` (기본 제공).

A default entry is installed for every active person (on enabling, on account activation, and at API
start by `services/connector_defaults.py`); people can switch it off but not remove it.

Data change, shipped rows only (matched by slug):

- `time`, `youtube`, `deep-research`: provided by default, enabled and listed unless the audit log
  shows an administrator saved that entry.
- Unsaved rows are attributed to the catalogue owner (oldest administrator) as `updated_by`; on a
  fresh install the first administrator takes them over.

Downgrade drops the column.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHIPPED = ("time", "youtube", "deep-research")


def _admin_saved(conn, slug: str) -> bool:
    """Whether an administrator saved this entry in the catalogue screen."""
    return conn.execute(
        sa.text("SELECT 1 FROM audit_events WHERE action = 'connector.catalog.save' "
                "AND target = :target LIMIT 1"),
        {"target": f"connector:{slug}"},
    ).first() is not None


def upgrade() -> None:
    op.add_column("connector_catalog", sa.Column(
        "by_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    conn = op.get_bind()
    owner = conn.execute(sa.text(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at, id LIMIT 1"
    )).scalar()
    catalog = sa.table(
        "connector_catalog",
        sa.column("slug", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("listed", sa.Boolean),
        sa.column("by_default", sa.Boolean),
        sa.column("updated_by", sa.String),
    )
    for slug in SHIPPED:
        values: dict = {"by_default": True}
        if not _admin_saved(conn, slug):
            values.update(enabled=True, listed=True)
        conn.execute(catalog.update().where(catalog.c.slug == slug).values(values))
    if owner:
        conn.execute(catalog.update().where(catalog.c.updated_by.is_(None))
                     .values(updated_by=owner))


def downgrade() -> None:
    op.drop_column("connector_catalog", "by_default")
