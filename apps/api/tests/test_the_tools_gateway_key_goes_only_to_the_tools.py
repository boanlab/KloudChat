"""The gateway's `/tools/*` key rides on every tool-backend request and nowhere else.

KloudChat-LLM's gateway answers `/tools/*` with 401 unless the request carries
`X-Tools-Key`. Each client below is the one a real call site uses; the transport is
replaced so every outgoing request is recorded with the headers it left with.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services import (
    chart_code,
    factcheck,
    index_client,
    mcp,
    netguard,
    sandbox,
    settings_store,
    tools_gateway,
    transcribe,
)
from app.services.tools import builtin, catalog

KEY = "k" * 64
GATEWAY = "http://gateway.test:8080"
BACKENDS = settings_store.ToolBackends(
    search=f"{GATEWAY}/tools/search",
    fetch=f"{GATEWAY}/tools/fetch",
    exec=f"{GATEWAY}/tools/exec",
    research=f"{GATEWAY}/tools/research",
    stt=f"{GATEWAY}/tools/stt",
    index=f"{GATEWAY}/tools/index",
)


def _answer(request: httpx.Request) -> httpx.Response:
    """Just enough of each backend's shape for the caller to go on."""
    path = request.url.path
    if path.endswith("/mcp"):
        body = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        return httpx.Response(200, json=body, headers={"mcp-session-id": "s1"})
    if path.endswith("/exec"):
        body = {
            "session_id": "s1",
            "stdout": "",
            "files": [{"id": "f1", "name": "chart.png", "session_id": "s1"}],
        }
        return httpx.Response(200, text=json.dumps(body))
    if "/download/" in path:
        return httpx.Response(200, content=b"\x89PNG")
    if path.endswith("/upload/batch"):
        body = {"session_id": "s1", "files": [{"fileId": "f1", "filename": "a.txt"}]}
        return httpx.Response(200, json=body)
    if path.endswith("/transcriptions"):
        return httpx.Response(200, json={"text": "안녕하세요", "language": "ko"})
    if path.endswith("/scrape"):
        return httpx.Response(200, json={"success": True, "data": {"markdown": "# page"}})
    return httpx.Response(200, json={"results": [], "passages": [], "chunks": 1})


@pytest.fixture
def sent(monkeypatch):
    """Every request that reached the transport, after the client's hooks ran."""
    seen: list[httpx.Request] = []

    async def handle(self, request):
        await request.aread()
        seen.append(request)
        return _answer(request)

    async def backends():
        return BACKENDS

    async def public(_url):
        return None

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    monkeypatch.setattr(settings_store, "tools_config", backends)
    monkeypatch.setattr(netguard, "refusal", public)
    monkeypatch.setattr(settings, "tools_gateway_key", KEY)
    return seen


def _all_carry_the_key(seen: list[httpx.Request]) -> None:
    assert seen, "no request was made"
    for request in seen:
        assert str(request.url).startswith(GATEWAY + "/tools/"), request.url
        assert request.headers.get("X-Tools-Key") == KEY, request.url


# ── the helper ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "base", "expected"),
    [
        (f"{GATEWAY}/tools/search/search?q=1", f"{GATEWAY}/tools/search", True),
        (f"{GATEWAY}/tools/search", f"{GATEWAY}/tools/search/", True),
        ("HTTP://GATEWAY.test:8080/tools/stt/v1", f"{GATEWAY}/tools/stt", True),
        ("https://kllm.example/tools/exec/exec", "https://kllm.example:443/tools/exec", True),
        # A sibling path that merely starts with the same letters
        (f"{GATEWAY}/tools/searchx", f"{GATEWAY}/tools/search", False),
        (f"{GATEWAY}/litellm/v1/models", f"{GATEWAY}/tools/search", False),
        ("http://gateway.test:8081/tools/search", f"{GATEWAY}/tools/search", False),
        ("https://gateway.test:8080/tools/search", f"{GATEWAY}/tools/search", False),
        ("http://gateway.test.evil.example:8080/tools/search", f"{GATEWAY}/tools/search", False),
        ("http://evil.example/tools/search", f"{GATEWAY}/tools/search", False),
        (f"{GATEWAY}/tools/search", "", False),
        (f"{GATEWAY}/tools/search", "not a url", False),
    ],
)
def test_under_matches_scheme_host_port_and_path_prefix(url, base, expected):
    assert tools_gateway.under(url, base) is expected


@pytest.mark.asyncio
async def test_the_client_adds_the_key_to_tool_addresses_only(sent):
    async with tools_gateway.client() as client:
        await client.get(f"{GATEWAY}/tools/index/health")
        await client.get("https://example.com/page", headers={"X-Tools-Key": "forged"})
        await client.get(f"{GATEWAY}/litellm/v1/models")
    assert [r.headers.get("X-Tools-Key") for r in sent] == [KEY, None, None]


@pytest.mark.asyncio
async def test_a_caller_cannot_choose_the_key_sent_to_a_tool(sent):
    async with tools_gateway.client() as client:
        await client.get(f"{GATEWAY}/tools/search/healthz", headers={"X-Tools-Key": "other"})
    assert sent[0].headers.get_list("X-Tools-Key") == [KEY]


@pytest.mark.asyncio
async def test_a_redirect_off_the_gateway_drops_the_key(monkeypatch, sent):
    async def handle(self, request):
        sent.append(request)
        if request.url.host == "gateway.test":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example/x"})
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    async with tools_gateway.client(follow_redirects=True) as client:
        await client.get(f"{GATEWAY}/tools/fetch/health")
    assert [r.headers.get("X-Tools-Key") for r in sent] == [KEY, None]


@pytest.mark.asyncio
async def test_no_key_configured_sends_nothing(monkeypatch, sent):
    monkeypatch.setattr(settings, "tools_gateway_key", "")
    async with tools_gateway.client() as client:
        await client.get(f"{GATEWAY}/tools/search/healthz")
    assert "X-Tools-Key" not in sent[0].headers


# ── every call site ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_sends_the_key(sent):
    await builtin._searxng(BACKENDS.search, "서울 날씨", 3)
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_document_fetch_sends_the_key_to_the_shim_not_the_page(sent):
    await builtin._scrape(BACKENDS.fetch, "https://example.com/article")
    _all_carry_the_key(sent)
    assert json.loads(sent[0].content)["url"] == "https://example.com/article"


@pytest.mark.asyncio
async def test_fact_check_search_sends_the_key(sent):
    await factcheck._search("대한민국 수도")
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_the_sandbox_sends_the_key_on_every_call(sent):
    client = await sandbox.client()
    assert client is not None
    await client.upload([("a.txt", b"hi")])
    await client.exec("print(1)")
    await client.list("s1")
    await client.download("s1", "f1")
    assert len(sent) == 4
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_chart_code_sends_the_key(sent):
    png, _ = await chart_code._run("print(1)")
    assert png
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_the_retrieval_index_sends_the_key(sent):
    collection, doc_id = "c" * 32, "d" * 32
    await index_client.put_document(collection=collection, doc_id=doc_id, name="n", text="t")
    await index_client.search(collection=collection, query="q")
    await index_client.forget_document(collection=collection, doc_id=doc_id)
    await index_client.forget_collection(collection=collection)
    assert len(sent) == 4
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_transcription_sends_the_key(sent):
    await transcribe._whisper(BACKENDS.stt, b"RIFF", "a.wav", "ko")
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_the_deep_research_connector_sends_the_key(sent):
    entry = catalog.catalog_entry("deep-research")
    endpoint = await catalog.resolve_urls(entry["endpoint"])
    assert endpoint == f"{BACKENDS.research}/mcp"
    await mcp.list_tools("http", endpoint)
    assert len(sent) == 3  # initialize, initialized, tools/list
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_someone_elses_mcp_server_never_sees_the_key(sent):
    await mcp.list_tools("http", "https://mcp.example.com/mcp")
    assert sent
    assert all("X-Tools-Key" not in r.headers for r in sent)


@pytest.mark.asyncio
async def test_an_mcp_connector_aimed_at_another_tool_path_gets_no_key(sent):
    await mcp.list_tools("http", f"{BACKENDS.exec}/mcp")
    assert sent
    assert all("X-Tools-Key" not in r.headers for r in sent)


@pytest.mark.asyncio
async def test_the_admin_connection_test_sends_the_key(sent):
    from app.routers import admin

    result = await admin.test_tool("exec", None)
    assert result["ok"]
    _all_carry_the_key(sent)


@pytest.mark.asyncio
async def test_the_admin_connection_test_reports_a_refused_key(monkeypatch, sent):
    from app.routers import admin

    async def refuse(self, request):
        return httpx.Response(401, json={"error": "unauthorized"})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    result = await admin.test_tool("search", None)
    assert not result["ok"]
    assert "TOOLS_GATEWAY_KEY" in result["detail"]


# ── the youtube connector, a stdio process ─────────────────────────────


class _Connector:
    def __init__(self, slug, env, official=True):
        self.slug = slug
        self.env = settings_store.encrypt_env(env)
        self.official = official


@pytest.mark.asyncio
async def test_the_youtube_connector_gets_the_key_for_the_stt_address(sent):
    env = await catalog.connector_env(_Connector("youtube", {"WHISPER_URL": "${TOOLS_STT_URL}"}))
    assert env == {"WHISPER_URL": BACKENDS.stt, "TOOLS_GATEWAY_KEY": KEY}


@pytest.mark.asyncio
async def test_a_youtube_connector_pointed_elsewhere_gets_no_key(sent):
    env = await catalog.connector_env(
        _Connector("youtube", {"WHISPER_URL": "https://whisper.example.com"})
    )
    assert "TOOLS_GATEWAY_KEY" not in env


@pytest.mark.asyncio
async def test_a_youtube_connector_pointed_at_another_tool_gets_no_key(sent):
    env = await catalog.connector_env(_Connector("youtube", {"WHISPER_URL": BACKENDS.exec}))
    assert "TOOLS_GATEWAY_KEY" not in env


@pytest.mark.asyncio
async def test_a_custom_connector_cannot_ask_for_the_key(sent):
    connector = _Connector(
        "youtube", {"WHISPER_URL": "${TOOLS_STT_URL}", "TOOLS_GATEWAY_KEY": "x"}, official=False
    )
    assert "TOOLS_GATEWAY_KEY" not in await catalog.connector_env(connector)
