"""The connector catalogue is data administrators keep in the UI; members install from it.

time and youtube, the two entries no one could make before, are made by an administrator
through the same endpoints the admin screen calls, and they install and run exactly as the
seeded rows (migration 0062) do — the gateway key still bound to the stt address only.
Stored values never come back."""

from __future__ import annotations

from types import SimpleNamespace

import catalog_seed
import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.user import AuditEvent, User, UserRole
from app.models.workspace import Connector, ConnectorCatalogEntry, ConnectorTool
from app.routers import connectors
from app.services import settings_store
from app.services.tools import catalog

GATEWAY = "http://gateway.test:8080"


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
            User, AuditEvent, ConnectorCatalogEntry, Connector, ConnectorTool)])
    backends = settings_store.ToolBackends(
        search="", fetch="", exec=f"{GATEWAY}/tools/exec", research=f"{GATEWAY}/tools/research",
        stt=f"{GATEWAY}/tools/stt", index="")

    async def tools_config():
        return backends

    async def no_server(*_a, **_k):
        raise RuntimeError("offline")

    monkeypatch.setattr(settings_store, "tools_config", tools_config)
    monkeypatch.setattr(settings, "tools_gateway_key", "k" * 64)
    monkeypatch.setattr(connectors.mcp, "list_tools", no_server)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
    catalog.use([])


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


async def _people(db):
    admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
    member = User(email="m@example.com", name="회원", password_hash="x")
    db.add_all([admin, member])
    await db.commit()
    return admin, member


def _entry_in(row: dict, position: int = 0) -> connectors.CatalogEntryIn:
    return connectors.CatalogEntryIn(
        slug=row["slug"], name=row["name"], description=row["description"],
        category=row["category"], transport=row["transport"], endpoint=row["endpoint"],
        env=dict(row["env"] or {}), tools_key_var=row["tools_key_var"],
        tools_key_feature=row["tools_key_feature"], kinds=row["kinds"], position=position)


async def test_an_admin_makes_time_and_youtube_and_they_run_as_the_seeded_rows(db, monkeypatch):
    admin, member = await _people(db)
    seeded = {row["slug"]: row for row in catalog_seed.seed()}
    for n, slug in enumerate(("time", "youtube")):
        await connectors.admin_add_entry(_entry_in(seeded[slug], n), admin, db, _request())
    listed = await connectors.get_catalog(member, db)
    assert [e["slug"] for e in listed] == ["time", "youtube"]
    # A member installs a stdio entry: an administrator put the command there.
    made = await connectors.install("youtube", member, db)
    row = await db.get(Connector, made.id)
    env = await catalog.connector_env(row)
    assert env == {"WHISPER_URL": f"{GATEWAY}/tools/stt", "TOOLS_GATEWAY_KEY": "k" * 64}
    assert await catalog.effective_endpoint(row) == seeded["youtube"]["endpoint"]
    # The same as the seed would give.
    made_entry = await catalog.entry("youtube")
    want = next(e for e in catalog_seed.entries() if e["slug"] == "youtube")
    assert {k: made_entry[k] for k in ("transport", "endpoint", "tools_key_for", "kinds")} == {
        k: want[k] for k in ("transport", "endpoint", "tools_key_for", "kinds")}
    assert settings_store.decrypt_env(made_entry["env"]) == want["env"]
    time_row = await db.get(Connector, (await connectors.install("time", member, db)).id)
    assert "TOOLS_GATEWAY_KEY" not in await catalog.connector_env(time_row)


async def test_stored_values_never_come_back_and_bad_entries_are_refused(db):
    admin, member = await _people(db)
    entry = connectors.CatalogEntryIn(
        slug="crm", name="CRM", transport="http", endpoint="https://crm.example.com/mcp",
        env={"API_TOKEN": "s3cret-value", "BASE": "${TOOLS_FETCH_URL}"})
    out = await connectors.admin_add_entry(entry, admin, db, _request())
    shown = next(e for e in out["entries"] if e["slug"] == "crm")
    assert shown["env"] == {"API_TOKEN": None, "BASE": "${TOOLS_FETCH_URL}"}
    assert "s3cret" not in str(out)
    # An edit that sends null keeps the stored secret.
    row = (await connectors.admin_catalog(admin, db))["entries"][0]
    again = entry.model_copy(update={"env": {"API_TOKEN": None, "BASE": "${TOOLS_FETCH_URL}"},
                                     "description": "고객 관리"})
    await connectors.admin_edit_entry(row["id"], again, admin, db, _request())
    stored = await db.get(ConnectorCatalogEntry, row["id"])
    assert settings_store.decrypt_env(stored.env)["API_TOKEN"] == "s3cret-value"
    for bad in (
        {"slug": "Bad Slug"}, {"transport": "ftp"}, {"endpoint": "file:///etc/passwd"},
        {"env": {"TOOLS_GATEWAY_KEY": "x"}}, {"tools_key_var": "BASE", "tools_key_feature": "ssh"},
        {"endpoint": "${TOOLS_NOPE_URL}/mcp"},
    ):
        with pytest.raises(HTTPException):
            await connectors.admin_add_entry(entry.model_copy(update={"slug": "x2", **bad}),
                                             admin, db, _request())
    # Members cannot reach the admin endpoints (the dependency refuses them); disabled entries
    # are not installable.
    off = entry.model_copy(update={"slug": "off", "enabled": False})
    await connectors.admin_add_entry(off, admin, db, _request())
    assert "off" not in [e["slug"] for e in await connectors.get_catalog(member, db)]
    with pytest.raises(HTTPException):
        await connectors.install("off", member, db)
