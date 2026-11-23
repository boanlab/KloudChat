"""MCP servers are shared by reference: the catalogue table is folded into `connectors`.

`connectors` gains `visibility` (the `agentvisibility` type skills use), `origin_id` (the shared
server a row refers to), `required_env`, `tools_key_var`, `tools_key_feature`, `by_default` and
`listed`; `official` is dropped, since a server is official while its sharer is an administrator.

Data change (needs an administrator; a fresh install has none and seeds at first sign-up):

- Each `connector_catalog` row becomes a shared server owned by the oldest administrator. Where
  that person already holds the catalogue's installed copy (same slug), that row is promoted in
  place, so its tools and switches stay; otherwise a new row is made, to be connected from the
  screen.
- Every other `official` row of that slug becomes a reference (`origin_id`). The catalogue's own
  address and env were merged into those rows at install; the reference keeps only the keys the
  entry asks each person for. A disabled entry becomes unlisted, as it was never offered again
  while copies kept running.
- An `official` row whose slug has no catalogue entry becomes the person's own row.

Downgrade restores nothing: the table and `official` are not recreated.

Revision ID: 0099
Revises: 0098
Create Date: 2026-11-22
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None

_VISIBILITY = sa.Enum("private", "org", name="agentvisibility", create_type=False)
_JSON = JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite")
_TRANSPORT = sa.Enum("stdio", "http", "sse", name="transport", create_type=False)
_STATUS = sa.Enum("connected", "disconnected", "needs_auth", "error", name="connectorstatus",
                  create_type=False)


def _connectors() -> sa.Table:
    return sa.table(
        "connectors",
        sa.column("id", sa.String), sa.column("owner_id", sa.String),
        sa.column("name", sa.String), sa.column("slug", sa.String),
        sa.column("description", sa.String), sa.column("category", sa.String),
        sa.column("transport", _TRANSPORT), sa.column("endpoint", sa.String),
        sa.column("env", _JSON), sa.column("auth_type", sa.String), sa.column("kinds", _JSON),
        sa.column("official", sa.Boolean), sa.column("installed", sa.Boolean),
        sa.column("enabled", sa.Boolean), sa.column("status", _STATUS),
        sa.column("error", sa.String), sa.column("visibility", _VISIBILITY),
        sa.column("origin_id", sa.String), sa.column("required_env", _JSON),
        sa.column("tools_key_var", sa.String), sa.column("tools_key_feature", sa.String),
        sa.column("by_default", sa.Boolean), sa.column("listed", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _fold(conn) -> None:
    owner = conn.execute(sa.text(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at, id LIMIT 1"
    )).scalar()
    if owner is None:
        return
    catalog = sa.table(
        "connector_catalog",
        sa.column("slug", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.String), sa.column("category", sa.String),
        sa.column("transport", sa.String), sa.column("endpoint", sa.String),
        sa.column("env", _JSON), sa.column("required_env", _JSON), sa.column("auth", sa.String),
        sa.column("tools_key_var", sa.String), sa.column("tools_key_feature", sa.String),
        sa.column("kinds", _JSON), sa.column("enabled", sa.Boolean),
        sa.column("listed", sa.Boolean), sa.column("by_default", sa.Boolean),
        sa.column("position", sa.Integer),
    )
    c = _connectors()
    now = datetime.now(UTC)
    slugs = set()
    for e in conn.execute(sa.select(catalog).order_by(catalog.c.position, catalog.c.name)):
        slugs.add(e.slug)
        required = list(e.required_env or [])
        asked = {str(f.get("key")) for f in required}
        shown = bool(e.enabled)
        fields = dict(
            name=e.name, description=e.description or "", category=e.category or "",
            transport=e.transport, endpoint=e.endpoint, env=dict(e.env or {}) or None,
            auth_type=e.auth or "none", kinds=list(e.kinds or ["chat"]), visibility="org",
            origin_id=None, required_env=required, tools_key_var=e.tools_key_var or "",
            tools_key_feature=e.tools_key_feature or "", listed=bool(e.listed) and shown,
            by_default=bool(e.by_default) and shown and not required, updated_at=now,
        )
        source = conn.execute(sa.select(c.c.id).where(
            c.c.owner_id == owner, c.c.slug == e.slug, c.c.official.is_(True)
        ).order_by(c.c.created_at).limit(1)).scalar()
        if source is None:
            source = str(uuid.uuid4())
            conn.execute(c.insert().values(
                id=source, owner_id=owner, slug=e.slug, official=False, installed=True,
                enabled=True, status="disconnected", created_at=now, **fields))
        else:
            conn.execute(c.update().where(c.c.id == source).values(official=False, **fields))
        for row in conn.execute(sa.select(c.c.id, c.c.env).where(
                c.c.slug == e.slug, c.c.official.is_(True), c.c.id != source)).all():
            own = {k: v for k, v in dict(row.env or {}).items() if k in asked}
            conn.execute(c.update().where(c.c.id == row.id).values(
                origin_id=source, endpoint="", env=own or None, official=False))
    # a copy of an entry that no longer exists is the person's own
    conn.execute(c.update().where(c.c.official.is_(True)).values(official=False))


def upgrade() -> None:
    op.add_column("connectors", sa.Column(
        "visibility", _VISIBILITY, nullable=False, server_default="private"))
    op.add_column("connectors", sa.Column("origin_id", sa.String(), nullable=True))
    op.add_column("connectors", sa.Column("required_env", _JSON, nullable=True))
    op.add_column("connectors", sa.Column(
        "tools_key_var", sa.String(length=60), nullable=False, server_default=""))
    op.add_column("connectors", sa.Column(
        "tools_key_feature", sa.String(length=20), nullable=False, server_default=""))
    op.add_column("connectors", sa.Column(
        "by_default", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("connectors", sa.Column(
        "listed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_connectors_origin_id", "connectors", ["origin_id"])
    _fold(op.get_bind())
    with op.batch_alter_table("connectors") as batch:
        batch.drop_column("official")
    op.drop_index("ix_connector_catalog_slug", table_name="connector_catalog")
    op.drop_table("connector_catalog")


def downgrade() -> None:
    # the catalogue rows and each copy's `official` mark are not kept anywhere
    pass
