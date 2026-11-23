"""기본 제공 connectors: shared servers of an administrator, installed for every active person."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.user import AuditEvent, User, UserRole, UserStatus
from app.models.workspace import Connector, ConnectorTool, Transport, Visibility
from app.routers import connectors
from app.schemas.workspace import BulkDelete, ConnectorPatch, RequiredEnvIn
from app.services import connector_defaults, settings_store
from app.services.tools import catalog

GATEWAY = "http://gateway.test:8080"
TIME_COMMAND = "uvx --with 'mcp<2' mcp-server-time --local-timezone=Asia/Seoul"
TOOLS = [
    {"name": "get_current_time", "description": "현재 시각",
     "inputSchema": {"type": "object", "properties": {"timezone": {"type": "string"}}}},
    {"name": "set_alarm", "description": "알람"},
]


@pytest.fixture
async def db(monkeypatch):
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[metadata.tables[m.__tablename__] for m in (
            User, AuditEvent, Connector, ConnectorTool)])
    backends = settings_store.ToolBackends(research=f"{GATEWAY}/tools/research",
                                           stt=f"{GATEWAY}/tools/stt")

    async def tools_config():
        return backends

    monkeypatch.setattr(settings_store, "tools_config", tools_config)
    monkeypatch.setattr(settings, "tools_gateway_key", "k" * 64)
    connector_defaults._DISCOVERED.clear()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
    catalog.use([])
    connector_defaults._DISCOVERED.clear()


def _server(monkeypatch, tools=TOOLS):
    asked: list[str] = []

    async def list_tools(transport, endpoint, env=None, **_kwargs):
        asked.append(endpoint)
        if tools is None:
            raise RuntimeError("offline")
        return tools

    monkeypatch.setattr(connector_defaults.mcp, "list_tools", list_tools)
    monkeypatch.setattr(connectors.mcp, "list_tools", list_tools)
    return asked


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


async def _people(db):
    admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin,
                 status=UserStatus.active)
    member = User(email="m@example.com", name="회원", password_hash="x",
                  status=UserStatus.active)
    waiting = User(email="w@example.com", name="대기", password_hash="x")
    db.add_all([admin, member, waiting])
    await db.commit()
    return admin, member, waiting


async def _share(db, owner, **over) -> Connector:
    """A shared server of `owner` (the time server unless said otherwise); the cache is loaded."""
    fields = {"slug": "time", "name": "시간", "transport": Transport.stdio,
              "endpoint": TIME_COMMAND, "kinds": ["chat", "report", "slides"],
              "visibility": Visibility.org, **over}
    if isinstance(fields.get("env"), dict):
        fields["env"] = settings_store.encrypt_env(fields["env"])
    row = Connector(owner_id=owner.id, **fields)
    db.add(row)
    await db.commit()
    await catalog.refresh(db)
    return row


async def _copies(db, slug="time") -> list[Connector]:
    return list((await db.exec(select(Connector).where(Connector.slug == slug))).all())


async def test_a_shared_default_is_installed_for_every_active_person_once(db, monkeypatch):
    asked = _server(monkeypatch)
    admin, member, waiting = await _people(db)
    shared = await _share(db, admin)
    assert await connector_defaults.provide_everyone(db) == 0 and len(await _copies(db)) == 1

    await connectors.patch(shared.id, ConnectorPatch(by_default=True), admin, db)
    assert await connector_defaults.provide_everyone(db) == 1
    copies = await _copies(db)
    assert sorted(c.owner_id for c in copies) == sorted([admin.id, member.id])
    assert waiting.id not in {c.owner_id for c in copies}
    mine = next(c for c in copies if c.owner_id == member.id)
    assert (mine.origin_id, mine.endpoint, mine.installed, mine.enabled, mine.status.value) == (
        shared.id, "", True, True, "connected")
    tools = {t.name: t for t in (await db.exec(
        select(ConnectorTool).where(ConnectorTool.connector_id == mine.id))).all()}
    # Write tools start off, as an install leaves them.
    assert (tools["get_current_time"].enabled, tools["set_alarm"].enabled) == (True, False)
    assert tools["get_current_time"].parameters["properties"] == {"timezone": {"type": "string"}}
    assert len(asked) == 1

    # Run again: nobody gets a second copy, and the server is not asked again.
    assert await connector_defaults.provide_everyone(db) == 0
    assert len(await _copies(db)) == 2 and len(asked) == 1

    # Someone who becomes active gets it then.
    waiting.status = UserStatus.active
    db.add(waiting)
    assert await connector_defaults.provide(db, waiting) == 1
    await db.commit()
    assert await connector_defaults.provide(db, waiting) == 0
    theirs = next(c for c in await _copies(db) if c.owner_id == waiting.id)
    assert len((await db.exec(
        select(ConnectorTool).where(ConnectorTool.connector_id == theirs.id))).all()) == 2
    assert len(asked) == 1

    # The sharer's own row gets its tools from the server, then serves the next copies.
    assert await connector_defaults.connect_owners(db) == 1
    await db.commit()
    await db.refresh(shared)
    assert shared.status.value == "connected"
    assert await connector_defaults.connect_owners(db) == 0


async def test_a_person_switches_it_off_but_cannot_remove_it(db, monkeypatch):
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin, by_default=True)
    await connector_defaults.provide_everyone(db)
    mine = next(c for c in await _copies(db) if c.owner_id == member.id)

    listed = await connectors.list_connectors(member, db)
    assert [(c.slug, c.by_default) for c in listed] == [("time", True)]
    assert listed[0].model_dump(by_alias=True)["byDefault"] is True
    shown = await connectors.get_store(member, db)
    assert [(e["slug"], e["installed"], e["byDefault"]) for e in shown] == [("time", True, True)]

    off = await connectors.patch(mine.id, ConnectorPatch(enabled=False), member, db)
    assert off.enabled is False
    for attempt in (
        connectors.uninstall(mine.id, member, db),
        connectors.patch(mine.id, ConnectorPatch(installed=False), member, db),
    ):
        with pytest.raises(HTTPException) as refused:
            await attempt
        assert (refused.value.status_code, refused.value.detail) == (409, "connector_default")
    # Bulk removal takes the person's own servers and leaves the default copy.
    own = Connector(owner_id=member.id, name="위키", slug="wiki", endpoint="https://w.example")
    db.add(own)
    await db.commit()
    gone = await connectors.uninstall_many(BulkDelete(ids=[mine.id, own.id]), member, db)
    assert gone == {"deleted": 1, "kept": 1}
    assert await db.get(Connector, mine.id) is not None
    # Installing it again returns the same copy.
    assert (await connectors.install(shared.id, member, db)).id == mine.id

    # Not 기본 제공 any more: the copy stays and is the person's to remove.
    await connectors.patch(shared.id, ConnectorPatch(by_default=False), admin, db)
    assert (await connectors.list_connectors(member, db))[0].by_default is False
    await connectors.uninstall(mine.id, member, db)
    assert await db.get(Connector, mine.id) is None


async def test_a_dead_server_leaves_copies_to_reconnect_and_credentials_cannot_be_default(
        db, monkeypatch):
    _server(monkeypatch, tools=None)
    admin, member, _ = await _people(db)
    await _share(db, admin, by_default=True)
    assert await connector_defaults.provide_everyone(db) == 1
    mine = next(c for c in await _copies(db) if c.owner_id == member.id)
    assert mine.status.value == "error" and mine.error
    # 다시 연결 once the server answers.
    _server(monkeypatch)
    synced = await connectors.sync(mine.id, member, db)
    assert synced.status.value == "connected" and len(synced.tools) == 2

    crm = await _share(db, admin, slug="crm", name="CRM", transport=Transport.http,
                       endpoint="https://crm.example.com/mcp")
    with pytest.raises(HTTPException) as refused:
        await connectors.patch(crm.id, ConnectorPatch(
            by_default=True, required_env=[RequiredEnvIn(key="API_TOKEN", secret=True)]),
            admin, db)
    assert (refused.value.status_code, refused.value.detail) == (422, "catalog_default_env")


async def test_a_disabled_or_credentialed_server_is_not_provided_and_startup_fills_the_gaps(
        db, monkeypatch):
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    off = await _share(db, admin, by_default=True, enabled=False)
    await _share(db, admin, slug="keyed", name="열쇠", by_default=True,
                 required_env=[{"key": "API_TOKEN", "label": "토큰"}])
    assert await connector_defaults.provide_everyone(db) == 0
    assert [c for c in await _copies(db, "time") if c.owner_id == member.id] == []
    off.enabled = True
    db.add(off)
    await db.commit()
    await _share(db, admin, slug="shipped", name="출시", endpoint="z", by_default=True)

    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *_):
            return None

    assert await connector_defaults.provide_at_startup(Factory()) == 2
    assert await connector_defaults.provide_at_startup(Factory()) == 0
    # The sharers' own rows were connected on the way.
    assert {c.status.value for c in await _copies(db) if c.owner_id == admin.id
            and c.slug in ("time", "shipped")} == {"connected"}
    assert [c.slug for c in await _copies(db, "keyed")] == ["keyed"]


async def test_the_first_administrator_shares_the_three_shipped_servers(db, monkeypatch):
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    assert await connector_defaults.seed_shipped(db, admin) == 3
    await db.commit()
    await catalog.refresh(db)
    rows = {r.slug: r for r in (await db.exec(select(Connector))).all()}
    assert set(rows) == {"time", "youtube", "deep-research"}
    assert all(r.visibility is Visibility.org and r.by_default and r.origin_id is None
               and r.owner_id == admin.id for r in rows.values())
    assert (rows["youtube"].tools_key_var, rows["youtube"].tools_key_feature) == (
        "WHISPER_URL", "stt")
    for r in rows.values():
        assert catalog.provided(await catalog.source(r.id))
    assert await connector_defaults.provide_everyone(db) == 3
    assert {c.slug for c in await connectors.list_connectors(member, db)} == set(rows)


# ── migration 0069 ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0069_default_connectors.py"
    spec = importlib.util.spec_from_file_location("default_connectors_0069", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database(conn, *, admins=(), saved=()):
    conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY, role VARCHAR, "
                         "created_at DATETIME)"))
    conn.execute(sa.text("CREATE TABLE audit_events (action VARCHAR, target VARCHAR)"))
    conn.execute(sa.text(
        "CREATE TABLE connector_catalog (slug VARCHAR PRIMARY KEY, enabled BOOLEAN, "
        "listed BOOLEAN, updated_by VARCHAR)"))
    # As 0062 and 0063 left them; `crm` an administrator made.
    conn.execute(sa.text(
        "INSERT INTO connector_catalog VALUES ('time', 1, 1, NULL), ('youtube', 1, 0, NULL), "
        "('deep-research', 0, 1, NULL), ('crm', 1, 1, 'u-crm')"))
    for n, (user_id, role) in enumerate(admins):
        conn.execute(sa.text("INSERT INTO users VALUES (:id, :role, :at)"),
                     {"id": user_id, "role": role, "at": f"2026-01-0{n + 1} 00:00:00"})
    for slug in saved:
        conn.execute(sa.text("INSERT INTO audit_events VALUES ('connector.catalog.save', :t)"),
                     {"t": f"connector:{slug}"})


def _catalog(conn) -> dict[str, tuple]:
    return {row[0]: tuple(row[1:]) for row in conn.execute(sa.text(
        "SELECT slug, enabled, listed, by_default, updated_by FROM connector_catalog"))}


def test_the_migration_provides_the_shipped_three_and_keeps_an_administrators_choice():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0069", "0068")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _database(conn, admins=[("u-member", "user"), ("u-first", "admin"),
                                ("u-later", "admin")], saved=["youtube"])
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert _catalog(conn) == {
            "time": (1, 1, 1, "u-first"),
            # Saved by an administrator: listed stays as they left it.
            "youtube": (1, 0, 1, "u-first"),
            "deep-research": (1, 1, 1, "u-first"),
            "crm": (1, 1, 0, "u-crm"),
        }
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert "by_default" not in [c["name"] for c in sa.inspect(conn).get_columns(
            "connector_catalog")]


def test_a_fresh_install_has_no_administrator_to_attribute_to():
    migration = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _database(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        assert _catalog(conn)["time"] == (1, 1, 1, None)
