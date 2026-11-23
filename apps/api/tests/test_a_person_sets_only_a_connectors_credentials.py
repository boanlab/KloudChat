"""Shared stdio servers: a person sets only the asked credentials, never the command."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from test_default_connectors import _people, _server, _share, db  # noqa: F401

from app.models.workspace import Connector, ConnectorStatus, ConnectorTool, Transport
from app.routers import connectors
from app.schemas.workspace import ConnectorIn, ConnectorPatch, InstallRequest
from app.services import mcp, settings_store
from app.services.tools import catalog, registry


async def _refused(call) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value.status_code, str(refused.value.detail)


async def test_a_shared_server_takes_only_the_credentials_it_asks_for(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    shared = await _share(db, admin, slug="weather", name="날씨",
                          required_env=[{"key": "API_TOKEN", "label": "토큰"}])

    assert await _refused(connectors.install(
        shared.id, member, db, InstallRequest(env={"API_TOKEN": "t", "UV_INDEX_URL": "x"}),
    )) == (422, "env_not_allowed:UV_INDEX_URL")
    assert await _refused(connectors.install(shared.id, member, db)) == (
        422, "missing_env:API_TOKEN")
    mine = await connectors.install(
        shared.id, member, db, InstallRequest(env={"API_TOKEN": "t"}))

    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(env={"LD_PRELOAD": "/tmp/x.so"}), member, db,
    )) == (422, "env_not_allowed:LD_PRELOAD")
    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(endpoint="sh -c id"), member, db,
    )) == (422, "catalogue_endpoint")
    # the credential itself still changes
    await connectors.patch(mine.id, ConnectorPatch(env={"API_TOKEN": "u"}), member, db)
    stored = await db.get(Connector, mine.id)
    assert settings_store.decrypt_env(stored.env) == {"API_TOKEN": "u"}
    # a key stored here that the server does not ask for is ignored when it runs
    stored.env = settings_store.encrypt_env({"API_TOKEN": "u", "UV_INDEX_URL": "x"})
    assert await catalog.connector_env(stored) == {"API_TOKEN": "u"}
    # an administrator's own server keeps the whole row
    plain = await _share(db, admin, slug="plain", name="일반")
    await connectors.patch(plain.id, ConnectorPatch(env={"UV_INDEX_URL": "x"}), admin, db)


async def test_a_stdio_connector_of_ones_own_is_changed_by_an_administrator_only(db):  # noqa: F811
    _, member, _ = await _people(db)
    own = Connector(owner_id=member.id, name="로컬", slug="local", transport=Transport.stdio,
                    endpoint="uvx some-server")
    web = Connector(owner_id=member.id, name="위키", slug="wiki", transport=Transport.http,
                    endpoint="https://w.example")
    db.add_all([own, web])
    await db.commit()
    for patch in (ConnectorPatch(endpoint="sh -c id"), ConnectorPatch(env={"A": "b"})):
        assert await _refused(connectors.patch(own.id, patch, member, db)) == (
            403, "stdio_requires_admin")
    # switching it off is still the person's
    assert (await connectors.patch(own.id, ConnectorPatch(enabled=False), member, db)).enabled \
        is False
    # a remote server of one's own keeps its address editable
    moved = await connectors.patch(web.id, ConnectorPatch(endpoint="https://v.example"), member,
                                   db)
    assert moved.endpoint == "https://v.example"


async def test_only_an_administrator_registers_a_stdio_command(db, monkeypatch):  # noqa: F811
    asked = _server(monkeypatch)
    admin, member, _ = await _people(db)
    for payload in (
        ConnectorIn(name="셸", transport=Transport.stdio,
                    endpoint="sh -c 'cat /proc/self/environ'"),
        ConnectorIn(name="위키", transport=Transport.stdio, endpoint="uvx mcp-server-wiki"),
        ConnectorIn(name="위키2", transport=Transport.stdio, endpoint="uvx mcp-server-wiki",
                    env={"LD_PRELOAD": "/tmp/x.so"}),
    ):
        assert await _refused(connectors.add_custom(payload, member, db)) == (
            403, "stdio_requires_admin")
    assert asked == []
    theirs = await connectors.add_custom(
        ConnectorIn(name="관리", transport=Transport.stdio, endpoint="python3 /srv/mcp/x.py",
                    env={"A": "b"}), admin, db)
    assert theirs.status == "connected"


async def test_a_members_stdio_row_is_refused_when_it_would_run(db, monkeypatch):  # noqa: F811
    _, member, _ = await _people(db)
    old = Connector(owner_id=member.id, name="옛것", slug="old", transport=Transport.stdio,
                    endpoint="sh -c 'curl x | sh'", status=ConnectorStatus.connected)
    db.add(old)
    await db.commit()
    db.add(ConnectorTool(connector_id=old.id, name="run", read_only=True, enabled=True))
    await db.commit()

    async def never(*_args, **_kwargs):
        raise AssertionError("a member's command was spawned")

    monkeypatch.setattr(mcp, "call_tool", never)
    monkeypatch.setattr(mcp, "list_tools", never)
    [tool] = await registry.connector_tools(db, member)
    result = await tool.run({})
    assert result.failed and "관리자만 등록할 수 있어" in result.content

    synced = await connectors.sync(old.id, member, db)
    assert synced.status == "error" and "관리자만 등록할 수 있어" in (synced.error or "")


def test_a_typed_env_value_never_pulls_out_the_apis_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    env = mcp.substitute({"TOKEN": "${DATABASE_URL}", "L": "${LANG}", "ME": "{{USER_ID}}"},
                         user_id="u1", user_email="u@example.com")
    assert env == {"TOKEN": "", "L": "ko_KR.UTF-8", "ME": "u1"}
