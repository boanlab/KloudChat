"""A catalogue entry can be provided to everyone: `connector_catalog.by_default` (기본 제공).

A default entry is installed for every active person: everyone at once when an administrator
turns it on, a new person when the account becomes active, and anyone still missing it when
the API starts (`services/connector_defaults.py`). People can switch it off but not remove it.

Data change, the shipped catalogue rows only (matched by slug; installed connectors, which are
rows of their own, are not touched here — the API installs them at start):

- `time`, `youtube`, `deep-research`: provided by default. Enabled and listed as well, unless
  the audit log shows an administrator saved that entry (their choice stands; a disabled
  entry is not installed for anyone).
- Rows no one has saved yet are attributed to the catalogue owner (the oldest administrator)
  as `updated_by`, like an entry an administrator made. A fresh install has no administrator
  yet; the first one takes them on signing up, or the API at start (bootstrap administrator).

Downgrade drops the column; the enabled, listed and attribution changes stay.

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
