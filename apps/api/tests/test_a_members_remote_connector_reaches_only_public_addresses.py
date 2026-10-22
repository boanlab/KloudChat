"""A member's own http/sse connector: public addresses only, at save and at connect time."""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from test_default_connectors import TOOLS, _people, _request, _time, db  # noqa: F401

from app.models.workspace import Connector, ConnectorStatus, Transport
from app.routers import connectors
from app.schemas.workspace import ConnectorIn, ConnectorPatch
from app.services import connector_defaults, mcp, netguard
from app.services.tools import catalog, registry

_DNS: dict[str, list[str]] = {}


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    _DNS.clear()
    _DNS.update({
        "mcp.example.com": ["93.184.216.34"],
        "intranet.example": ["10.40.0.14"],
        "ula.example": ["fd12:3456::7"],
    })

    async def resolve(host: str) -> list[str]:
        return list(_DNS.get(host, []))

    monkeypatch.setattr(netguard, "_system_resolver", resolve)


def _server(monkeypatch) -> list[tuple[str, bool]]:
    """Fake MCP server; records each endpoint asked and whether it was guarded."""
    asked: list[tuple[str, bool]] = []

    async def list_tools(transport, endpoint, env=None, public_only=False):
        asked.append((endpoint, public_only))
        return TOOLS

    monkeypatch.setattr(connector_defaults.mcp, "list_tools", list_tools)
    monkeypatch.setattr(connectors.mcp, "list_tools", list_tools)
    return asked


async def _refused(call) -> tuple[int, str]:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value.status_code, str(refused.value.detail)


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:1/x",  # loopback
    "http://localhost:8000/mcp",
    "http://[::1]/mcp",
    "http://169.254.169.254/latest/meta-data/",  # metadata
    "http://[fe80::1]/mcp",  # link-local
    "http://10.0.0.5/mcp",  # RFC 1918
    "http://172.18.0.5:8000/mcp",
    "http://192.168.1.1/mcp",
    "http://[fd00::1]/mcp",  # ULA
    "http://ula.example/mcp",
    "http://intranet.example/mcp",  # a name resolving inside
    "http://litellm:8000/mcp",  # the deployment's own services
    "http://kloudchat-api:8000/mcp",
    "http://host.docker.internal:8080/tools/research/mcp",
    "${TOOLS_RESEARCH_URL}/mcp",  # placeholder for an internal tool address
    "file:///etc/passwd",
])
async def test_a_member_cannot_save_an_internal_endpoint(db, monkeypatch, endpoint):  # noqa: F811
    asked = _server(monkeypatch)
    _, member, _ = await _people(db)
    code, detail = await _refused(connectors.add_custom(
        ConnectorIn(name="탐침", transport=Transport.http, endpoint=endpoint), member, db))
    assert code == 422
    assert detail in (catalog.INTERNAL_ENDPOINT, netguard.SCHEME)
    assert asked == []


async def test_a_member_cannot_move_a_connector_to_an_internal_endpoint(
        db, monkeypatch):  # noqa: F811
    asked = _server(monkeypatch)
    _, member, _ = await _people(db)
    mine = await connectors.add_custom(
        ConnectorIn(name="위키", transport=Transport.sse, endpoint="https://mcp.example.com/sse"),
        member, db)
    assert mine.status == "connected"
    assert asked == [("https://mcp.example.com/sse", True)]
    assert await _refused(connectors.patch(
        mine.id, ConnectorPatch(endpoint="http://127.0.0.1:8100/"), member, db,
    )) == (422, catalog.INTERNAL_ENDPOINT)


async def test_an_administrator_registers_an_internal_endpoint(db, monkeypatch):  # noqa: F811
    asked = _server(monkeypatch)
    admin, _, _ = await _people(db)
    theirs = await connectors.add_custom(
        ConnectorIn(name="사내", transport=Transport.http, endpoint="http://10.0.0.5/mcp"),
        admin, db)
    assert theirs.status == "connected" and asked == [("http://10.0.0.5/mcp", False)]
    moved = await connectors.patch(
        theirs.id, ConnectorPatch(endpoint="http://litellm:8000/mcp"), admin, db)
    assert moved.endpoint == "http://litellm:8000/mcp"


async def test_a_catalogue_connector_keeps_its_internal_host_for_a_member(
        db, monkeypatch):  # noqa: F811
    asked = _server(monkeypatch)
    admin, member, _ = await _people(db)
    await connectors.admin_add_entry(_time(
        slug="research", transport="http", endpoint="${TOOLS_RESEARCH_URL}/mcp"),
        admin, db, _request())
    mine = await connectors.install("research", member, db)
    assert mine.status == "connected"
    assert asked[-1] == ("http://gateway.test:8080/tools/research/mcp", False)


async def test_a_name_that_turns_internal_after_saving_is_refused_at_connect_time(
        db, monkeypatch):  # noqa: F811
    _server(monkeypatch)
    _, member, _ = await _people(db)
    mine = await connectors.add_custom(
        ConnectorIn(name="위키", transport=Transport.http, endpoint="https://mcp.example.com/mcp"),
        member, db)

    async def never(*_args, **_kwargs):
        raise AssertionError("an internal address was called")

    monkeypatch.setattr(mcp, "call_tool", never)
    monkeypatch.setattr(mcp, "list_tools", never)
    _DNS["mcp.example.com"] = ["127.0.0.1"]
    tool = next(t for t in await registry.connector_tools(db, member)
                if t.name.endswith("get_current_time"))
    result = await tool.run({})
    assert result.failed and catalog.INTERNAL_ENDPOINT in result.content

    synced = await connectors.sync(mine.id, member, db)
    assert synced.status == "error" and catalog.INTERNAL_ENDPOINT in (synced.error or "")


async def test_a_row_saved_before_the_rule_is_refused_when_it_would_connect(
        db, monkeypatch):  # noqa: F811
    _, member, _ = await _people(db)
    old = Connector(owner_id=member.id, name="옛것", slug="old", transport=Transport.http,
                    endpoint="http://169.254.169.254/latest/", status=ConnectorStatus.connected)
    db.add(old)
    await db.commit()

    async def never(*_args, **_kwargs):
        raise AssertionError("an internal address was called")

    monkeypatch.setattr(connectors.mcp, "list_tools", never)
    synced = await connectors.sync(old.id, member, db)
    assert synced.status == "error" and catalog.INTERNAL_ENDPOINT in (synced.error or "")


async def test_a_members_connector_dials_only_the_public_address_it_checked(monkeypatch):
    dialed: list[httpx.Request] = []

    async def handle(self, request):
        dialed.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
                              request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    assert await mcp.list_tools("http", "https://mcp.example.com/mcp", public_only=True) == []
    first = dialed[0]
    # the checked address is dialed; the server still sees its own name
    assert first.url.host == "93.184.216.34"
    assert first.headers["host"] == "mcp.example.com"
    assert first.extensions["sni_hostname"] == "mcp.example.com"

    # a rebinding name between two requests of one call
    dialed.clear()
    _DNS["mcp.example.com"] = ["10.0.0.9"]
    with pytest.raises(httpx.ConnectError, match=netguard.INTERNAL):
        await mcp.list_tools("http", "https://mcp.example.com/mcp", public_only=True)
    assert dialed == []


async def test_a_redirect_to_an_internal_address_is_not_dialed(monkeypatch):
    dialed: list[str] = []

    async def handle(self, request):
        dialed.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/admin"},
                              request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    async with httpx.AsyncClient(transport=netguard.PublicTransport(),
                                 follow_redirects=True) as client:
        with pytest.raises(httpx.ConnectError, match=netguard.INTERNAL):
            await client.get("https://mcp.example.com/mcp")
    assert dialed == ["https://93.184.216.34/mcp"]
