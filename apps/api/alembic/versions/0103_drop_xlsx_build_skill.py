"""The shipped skill 엑셀 파일 만들기 (`xlsx-build`) is removed from the catalogue; its rows and
references are cleaned.

The sheet surface and its agent tools (`xlsx_write`, `xlsx_edit`) do what the skill did, without
the sandbox: a table (pasted, or copied from an attached CSV or Excel file) becomes a workbook
whose totals and ratios are formulas, with number formats, widths and an Excel chart on request.
Its package folder is gone, so a row that still names it would point at nothing (and a copy
needing its script could not run it).

Data change, by `catalog_key` (the same rules as 0100):

- The catalogue row (no `origin_id`, owned by the oldest administrator) is deleted unless its
  administrator edited it (`package_hash` empty, or the content no longer matches it); an edited
  one stays as the administrator's own skill, and so do its copies.
- A copy is deleted unless its person changed it (name, description, when to use or body differ
  from its origin's, or the origin is gone and that cannot be told) or it carries scripts of its
  own. A copy that stays is cut loose: `catalog_key`, `package_hash` and the shipped-script
  `server_fields` are cleared, and `origin_id` too when its origin is deleted.
- Ids of deleted skills are taken out of `agents.skill_ids`, `agents.active_skill_ids`,
  `projects.skill_ids` and `sessions.skill_ids` (an allowlist that held only them stays `[]`),
  and the key out of `agents.check_skills` and `user_templates.check_skills`.

Counts are logged. Downgrade restores nothing.

Revision ID: 0103
Revises: 0102
Create Date: 2026-11-24
"""

from __future__ import annotations

import hashlib
import json
import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

KEYS = ("xlsx-build",)
_JSON = JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite")
#: what a package sets on its row (`services/starter._CONTENT`), for the hash at last sync
_CONTENT = ("name", "description", "when_to_use", "body", "version", "kinds", "required_tools")
_OWN = ("name", "description", "when_to_use", "body")


def _skills() -> sa.Table:
    return sa.table(
        "skills",
        sa.column("id", sa.String), sa.column("owner_id", sa.String),
        sa.column("catalog_key", sa.String), sa.column("origin_id", sa.String),
        sa.column("package_hash", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.String), sa.column("when_to_use", sa.String),
        sa.column("body", sa.String), sa.column("version", sa.String),
        sa.column("kinds", _JSON), sa.column("required_tools", _JSON),
        sa.column("server_fields", _JSON), sa.column("script_files", _JSON),
    )


def _hash(row) -> str:
    content = tuple(list(v) if isinstance(v := getattr(row, f), (list, tuple)) else v
                    for f in _CONTENT)
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, default=str).encode()
                          ).hexdigest()[:16]


def _same(row, other) -> bool:
    return all((getattr(row, f) or "") == (getattr(other, f) or "") for f in _OWN)


def _prune(conn, table_name: str, column: str, drop: set[str]) -> int:
    """Rows of `table_name` whose JSON list `column` held any of `drop`; their lists rewritten."""
    table = sa.table(table_name, sa.column("id", sa.String), sa.column(column, _JSON))
    col = table.c[column]
    changed = 0
    for row in conn.execute(sa.select(table.c.id, col).where(col.is_not(None))).all():
        held = row[1]
        if not isinstance(held, list) or not any(isinstance(v, str) and v in drop for v in held):
            continue
        conn.execute(table.update().where(table.c.id == row[0]).values(
            {column: [v for v in held if not (isinstance(v, str) and v in drop)]}))
        changed += 1
    return changed


def _clean(conn) -> None:
    skills = _skills()
    rows = conn.execute(sa.select(skills).where(skills.c.catalog_key.in_(KEYS))).all()
    if not rows:
        log.info("0103: no row of the removed skill")
        return
    owner = conn.execute(sa.text(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at, id LIMIT 1"
    )).scalar()
    #: key → its catalogue row: the oldest administrator's, else the one with a package hash
    catalogue: dict[str, object] = {}
    for key in KEYS:
        roots = [r for r in rows if r.catalog_key == key and r.origin_id is None]
        pick = [r for r in roots if r.owner_id == owner] or [r for r in roots if r.package_hash]
        if pick:
            catalogue[key] = pick[0]
    by_id = {r.id: r for r in rows}

    delete: set[str] = set()
    for row in rows:
        own_scripts = bool(row.script_files)
        if any(row is c for c in catalogue.values()):
            hashed = row.package_hash
            edited = bool(hashed == "" or (hashed and _hash(row) != hashed))
        else:
            origin = by_id.get(row.origin_id) if row.origin_id else catalogue.get(row.catalog_key)
            if origin is None and row.origin_id:
                origin = conn.execute(sa.select(skills).where(
                    skills.c.id == row.origin_id)).first()
            edited = origin is None or not _same(row, origin)
        if not (edited or own_scripts):
            delete.add(row.id)

    # a catalogue row that stays keeps its copies, too
    for key, root in catalogue.items():
        if root.id not in delete:
            delete -= {r.id for r in rows if r.catalog_key == key or r.origin_id == root.id}
    keep = [r for r in rows if r.id not in delete]
    log.info("0103: %d skill rows carry the removed key: %d deleted, %d kept as the person's own",
             len(rows), len(delete), len(keep))

    for row in keep:
        values = {"catalog_key": None, "package_hash": None, "server_fields": None}
        if row.origin_id in delete:
            values["origin_id"] = None
        conn.execute(skills.update().where(skills.c.id == row.id).values(**values))
        log.info("0103: kept skill %s (%s) as its owner's own", row.id, row.name)

    if delete:
        for table, column in (("agents", "skill_ids"), ("agents", "active_skill_ids"),
                              ("projects", "skill_ids"), ("sessions", "skill_ids")):
            log.info("0103: %s.%s: %d rows lose a deleted skill id", table, column,
                     _prune(conn, table, column, delete))
        conn.execute(skills.delete().where(skills.c.id.in_(delete)))
    for table in ("agents", "user_templates"):
        log.info("0103: %s.check_skills: %d rows lose the removed key", table,
                 _prune(conn, table, "check_skills", set(KEYS)))


def upgrade() -> None:
    _clean(op.get_bind())


def downgrade() -> None:
    # the rows and their references are not kept anywhere
    pass
