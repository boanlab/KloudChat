"""Shared servers: an administrator shares them, members install them as references."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from test_default_connectors import (  # noqa: F401
    GATEWAY,
    _people,
    _server,
    _share,
    db,
)

from app.models.workspace import Connector, Transport
from app.routers import connectors
from app.schemas.workspace import ConnectorIn, ConnectorPatch
from app.services import connector_defaults, settings_store
from app.services.tools import catalog


def _shipped(slug: str) -> dict:
    return next(s for s in connector_defaults.SHIPPED if s["slug"] == slug)


async def _share_shipped(session, owner, slug: str) -> Connector:
    spec = _shipped(slug)
    return await _share(
        session, owner, slug=slug, name=spec["name"], transport=Transport(spec["transport"]),
        endpoint=spec["endpoint"], env=spec["env"], kinds=spec["kinds"],
        tools_key_var=spec["tools_key_var"], tools_key_feature=spec["tools_key_feature"])


async def test_an_admin_shares_time_and_youtube_and_they_run_from_the_shared_rows(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    time = await _share_shipped(db, admin, "time")
    youtube = await _share_shipped(db, admin, "youtube")
    listed = await connectors.get_store(member, db)
    assert sorted(e["slug"] for e in listed) == ["time", "youtube"]
    assert all(e["official"] and not e["installed"] for e in listed)

    # A member installs a stdio server: an administrator put the command there.
    made = await connectors.install(youtube.id, member, db)
    row = await db.get(Connector, made.id)
    assert (row.origin_id, row.endpoint, row.env) == (youtube.id, "", None)
    env = await catalog.connector_env(row)
    assert env == {"WHISPER_URL": f"{GATEWAY}/tools/stt", "TOOLS_GATEWAY_KEY": "k" * 64}
    assert await catalog.effective_endpoint(row) == _shipped("youtube")["endpoint"]
    assert await catalog.effective_transport(row) == "stdio"
    time_row = await db.get(Connector, (await connectors.install(time.id, member, db)).id)
    assert "TOOLS_GATEWAY_KEY" not in await catalog.connector_env(time_row)


async def test_stored_values_never_come_back_and_bad_shares_are_refused(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    mine = await connectors.add_custom(ConnectorIn(
        name="CRM", transport=Transport.http, endpoint="https://crm.example.com/mcp",
        env={"API_TOKEN": "s3cret-value", "BASE": "${TOOLS_FETCH_URL}"}), admin, db)
    shared = await connectors.patch(mine.id, ConnectorPatch(visibility="org"), admin, db)
    assert shared.visibility == "org"
    assert shared.shared_env == {"API_TOKEN": None, "BASE": "${TOOLS_FETCH_URL}"}
    assert "s3cret" not in shared.model_dump_json()
    # An edit that leaves the env out keeps the stored secret.
    await connectors.patch(mine.id, ConnectorPatch(description="고객 관리"), admin, db)
    stored = await db.get(Connector, mine.id)
    assert settings_store.decrypt_env(stored.env)["API_TOKEN"] == "s3cret-value"
    # What a person who installs it is shown carries no value of the sharer's.
    seen = await connectors.install(mine.id, member, db)
    assert "s3cret" not in seen.model_dump_json() and seen.env_keys == []

    for bad in ({"endpoint": "file:///etc/passwd"}, {"endpoint": "${TOOLS_NOPE_URL}/mcp"},
                {"tools_key_var": "BASE", "tools_key_feature": "ssh"},
                {"tools_key_var": "NOPE", "tools_key_feature": "stt"}):
        with pytest.raises(HTTPException) as refused:
            await connectors.patch(mine.id, ConnectorPatch(**bad), admin, db)
        assert refused.value.status_code == 422, bad
    # The gateway key is the server's to add, never a variable a sharer can set.
    stored.env = settings_store.encrypt_env({"TOOLS_GATEWAY_KEY": "x"})
    db.add(stored)
    await db.commit()
    await catalog.refresh(db)
    assert "TOOLS_GATEWAY_KEY" not in await catalog.connector_env(
        await db.get(Connector, seen.id))
    # A server switched off is not installable; one never shared is not found either.
    stored.enabled = False
    db.add(stored)
    await db.commit()
    await catalog.refresh(db)
    other = Connector(owner_id=admin.id, name="비공개", slug="priv", transport=Transport.http,
                      endpoint="https://p.example.com")
    db.add(other)
    await db.commit()
    for source_id in (other.id, "nope"):
        with pytest.raises(HTTPException) as refused:
            await connectors.install(source_id, member, db)
        assert refused.value.status_code == 404
    assert [e["slug"] for e in await connectors.get_store(member, db)] == []
