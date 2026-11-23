"""Migration 0099: the catalogue table is folded into `connectors` as shared servers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.services import settings_store


def _migration():
    path = next((Path(__file__).parents[1] / "alembic" / "versions").glob("0099_*.py"))
    spec = importlib.util.spec_from_file_location("shared_connectors_0099", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sealed(**env: str) -> dict[str, str]:
    return settings_store.encrypt_env(env)


def _before(conn, *, admins: bool = True) -> None:
    """The schema as 0098 leaves it, with a few people, a catalogue and copies."""
    conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY, role VARCHAR, "
                         "created_at DATETIME)"))
    conn.execute(sa.text(
        "CREATE TABLE connector_catalog (slug VARCHAR PRIMARY KEY, name VARCHAR, "
        "description VARCHAR, category VARCHAR, transport VARCHAR, endpoint VARCHAR, env JSON, "
        "required_env JSON, auth VARCHAR, tools_key_var VARCHAR, tools_key_feature VARCHAR, "
        "kinds JSON, enabled BOOLEAN, listed BOOLEAN, by_default BOOLEAN, position INTEGER)"))
    conn.execute(sa.text("CREATE INDEX ix_connector_catalog_slug ON connector_catalog (slug)"))
    conn.execute(sa.text(
        "CREATE TABLE connectors (id VARCHAR PRIMARY KEY, owner_id VARCHAR, name VARCHAR, "
        "slug VARCHAR, description VARCHAR, category VARCHAR, transport VARCHAR, "
        "endpoint VARCHAR, env JSON, auth_type VARCHAR, kinds JSON, official BOOLEAN, "
        "installed BOOLEAN, enabled BOOLEAN, status VARCHAR, error VARCHAR, "
        "created_at DATETIME, updated_at DATETIME)"))
    conn.execute(sa.text("CREATE TABLE connector_tools (id VARCHAR PRIMARY KEY, "
                         "connector_id VARCHAR, name VARCHAR)"))
    people = [("u-member", "user", "2026-01-02 00:00:00"), ("u-late", "admin",
                                                          "2026-03-01 00:00:00"),
              ("u-other", "user", "2026-01-03 00:00:00")]
    if admins:
        people.append(("u-first", "admin", "2026-01-01 00:00:00"))
    for uid, role, at in people:
        conn.execute(sa.text("INSERT INTO users VALUES (:i, :r, :a)"),
                     {"i": uid, "r": role, "a": at})

    entries = [
        # (slug, name, transport, endpoint, env, required, enabled, listed, by_default)
        ("time", "시간", "stdio", "uvx mcp-server-time", {}, [], True, True, True),
        ("crm", "CRM", "http", "https://crm.example.com/mcp",
         _sealed(BASE="https://crm.example.com", API_TOKEN="catalogue-secret"),
         [{"key": "API_TOKEN", "label": "토큰", "hint": "", "secret": True}], True, True, True),
        ("old", "꺼진 것", "http", "https://old.example.com/mcp", {}, [], False, True, True),
    ]
    for n, (slug, name, transport, endpoint, env, required, enabled, listed, by_default) in \
            enumerate(entries):
        conn.execute(sa.text(
            "INSERT INTO connector_catalog VALUES (:slug, :name, 'd', 'c', :t, :e, :env, :req, "
            "'none', '', '', :kinds, :on, :listed, :default, :n)"),
            {"slug": slug, "name": name, "t": transport, "e": endpoint,
             "env": json.dumps(env), "req": json.dumps(required),
             "kinds": json.dumps(["chat"]), "on": enabled, "listed": listed,
             "default": by_default, "n": n})

    copies = [
        # (id, owner, slug, endpoint, env, official)
        ("a-time", "u-first", "time", "uvx mcp-server-time", {}, True),
        ("a-late-time", "u-late", "time", "uvx mcp-server-time", {}, True),
        ("m-time", "u-member", "time", "uvx mcp-server-time", {"STRAY": "enc"}, True),
        ("m-crm", "u-member", "crm", "https://crm.example.com/mcp",
         {**_sealed(BASE="https://crm.example.com", API_TOKEN="catalogue-secret"),
          "API_TOKEN": settings_store.encrypt_secret("mine")}, True),
        ("o-gone", "u-other", "retired", "uvx retired", {"K": "v"}, True),
        ("o-own", "u-other", "time", "uvx my-own-time", {"K": "v"}, False),
    ]
    for cid, owner, slug, endpoint, env, official in copies:
        conn.execute(sa.text(
            "INSERT INTO connectors VALUES (:i, :o, :n, :s, 'd', 'c', 'stdio', :e, :env, 'none', "
            "'[\"chat\"]', :off, 1, 1, 'connected', NULL, '2026-02-01 00:00:00', "
            "'2026-02-01 00:00:00')"),
            {"i": cid, "o": owner, "n": slug, "s": slug, "e": endpoint,
             "env": json.dumps(env) if env else None, "off": official})
    conn.execute(sa.text("INSERT INTO connector_tools VALUES ('t1', 'a-time', 'get_current_time')"))


def _run(conn) -> None:
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0099", "0098")
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()


def _rows(conn) -> dict[str, sa.Row]:
    return {r.id: r for r in conn.execute(sa.text("SELECT * FROM connectors"))}


def _env(row) -> dict:
    raw = row.env
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def test_the_administrators_copy_becomes_the_shared_server_and_keeps_its_tools():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _run(conn)
        rows = _rows(conn)
        shared = rows["a-time"]
        assert (shared.owner_id, shared.visibility, shared.origin_id, shared.by_default,
                shared.listed) == ("u-first", "org", None, 1, 1)
        assert (shared.status, shared.endpoint) == ("connected", "uvx mcp-server-time")
        # promoted in place: its tools are still its own
        assert conn.execute(sa.text(
            "SELECT connector_id FROM connector_tools")).scalar() == "a-time"
        assert "official" not in rows["a-time"]._mapping
        # the catalogue table and its index are gone
        assert "connector_catalog" not in sa.inspect(conn).get_table_names()


def test_other_peoples_catalogue_copies_become_references_keeping_only_asked_keys():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _run(conn)
        rows = _rows(conn)
        # every other copy of the slug refers to the shared one; the address is not kept
        for cid in ("a-late-time", "m-time"):
            assert (rows[cid].origin_id, rows[cid].endpoint) == ("a-time", ""), cid
            assert rows[cid].visibility == "private" and not rows[cid].by_default
        assert _env(rows["m-time"]) == {} and rows["m-time"].env in (None, "null")
        # crm: the catalogue's values are dropped from the copy, the person's own key stays
        crm_shared = next(r for r in rows.values() if r.slug == "crm" and r.origin_id is None
                          and r.owner_id == "u-first")
        assert (crm_shared.visibility, crm_shared.status) == ("org", "disconnected")
        assert crm_shared.id != "m-crm" and crm_shared.by_default == 0  # asks credentials
        assert json.loads(crm_shared.required_env)[0]["key"] == "API_TOKEN"
        assert set(_env(crm_shared)) == {"BASE", "API_TOKEN"}
        mine = rows["m-crm"]
        assert (mine.origin_id, mine.endpoint) == (crm_shared.id, "")
        assert set(_env(mine)) == {"API_TOKEN"}
        assert settings_store.decrypt_env(_env(mine)) == {"API_TOKEN": "mine"}
        # a disabled entry becomes unlisted and not a default; copies keep running
        old = next(r for r in rows.values() if r.slug == "old")
        assert (old.listed, old.by_default, old.visibility, old.owner_id) == (
            0, 0, "org", "u-first")


def test_a_copy_of_an_entry_that_no_longer_exists_is_the_persons_own():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn)
        _run(conn)
        rows = _rows(conn)
        gone, own = rows["o-gone"], rows["o-own"]
        for row in (gone, own):
            assert (row.origin_id, row.visibility, row.by_default) == (None, "private", 0)
        assert (gone.endpoint, _env(gone)) == ("uvx retired", {"K": "v"})
        assert own.endpoint == "uvx my-own-time"
        # no row is left claiming a mark that no longer exists, and none was added to people
        assert {r.owner_id for r in rows.values() if r.visibility == "org"} == {"u-first"}


def test_without_an_administrator_nothing_is_made_and_the_table_is_dropped():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _before(conn, admins=False)
        conn.execute(sa.text("DELETE FROM users WHERE role = 'admin'"))
        before = {r.id: (r.owner_id, r.slug, r.endpoint) for r in _rows(conn).values()}
        _run(conn)
        rows = _rows(conn)
        assert {i: (r.owner_id, r.slug, r.endpoint) for i, r in rows.items()} == before
        assert all(r.origin_id is None and r.visibility == "private" for r in rows.values())
        assert "connector_catalog" not in sa.inspect(conn).get_table_names()
        assert {"visibility", "origin_id", "required_env", "tools_key_var", "tools_key_feature",
                "by_default", "listed"} <= {c["name"] for c in sa.inspect(conn).get_columns(
                    "connectors")}
        assert "official" not in {c["name"] for c in sa.inspect(conn).get_columns("connectors")}
