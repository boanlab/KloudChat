"""A person could set any variable on a catalogue stdio connector (기본 제공 time, youtube): the
server process runs `uvx`/`uv` on the API host, and a variable such as an index URL or a preload
decides what code it runs. A person now sets only the credentials the entry asks for, never the
command; a person's own stdio connector stays the administrator's, as when adding one."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from test_default_connectors import _people, _request, _server, _time, db  # noqa: F401

from app.models.workspace import Connector, Transport
from app.routers import connectors
from app.schemas.workspace import ConnectorPatch, InstallRequest


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
