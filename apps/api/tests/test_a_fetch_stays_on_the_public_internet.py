"""A page fetch made on someone's behalf reaches the public internet and nothing behind it.

The fetch runs from inside the deployment. Without this, a URL a person typed or a model
picked could probe the database, LiteLLM, the tool gateway or a cloud metadata address.
"""

from __future__ import annotations

import pytest

from app.models.workspace import Project
from app.routers import workspace
from app.schemas.workspace import KnowledgeUrl
from app.services import netguard
from app.services.tools import builtin

_DNS = {
    "example.com": ["93.184.216.34"],
    "v6.example": ["2606:2800:220:1:248:1893:25c8:1946"],
    "intranet.example": ["10.40.0.14"],
    "mixed.example": ["93.184.216.34", "127.0.0.1"],
    "mapped.example": ["::ffff:10.0.0.7"],
    "cgnat.example": ["100.64.1.2"],
    "linklocal.example": ["fe80::1%eth0"],
}


async def _resolve(host: str) -> list[str]:
    return _DNS.get(host, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/page",
        "http://example.com:8080/page?q=1",
        "https://v6.example/",
        "https://8.8.8.8/",
        "https://[2606:4700::1111]/",
    ],
)
async def test_a_public_address_is_allowed(url):
    assert await netguard.refusal(url, resolve=_resolve) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("file:///etc/passwd", netguard.SCHEME),
        ("ftp://example.com/", netguard.SCHEME),
        ("raw:<script>", netguard.SCHEME),
        ("http://user:pw@example.com/", netguard.SCHEME),
        ("http://localhost:8100/api/health", netguard.INTERNAL),
        ("http://127.0.0.1:8100/api/health", netguard.INTERNAL),
        ("http://[::1]:8100/", netguard.INTERNAL),
        ("http://0.0.0.0/", netguard.INTERNAL),
        ("http://10.40.0.14:5433/", netguard.INTERNAL),
        ("http://192.168.1.1/", netguard.INTERNAL),
        ("http://172.18.0.5:8000/", netguard.INTERNAL),
        ("http://169.254.169.254/latest/meta-data/", netguard.INTERNAL),
        ("http://[fd00::1]/", netguard.INTERNAL),
        ("http://[::ffff:127.0.0.1]/", netguard.INTERNAL),
        ("http://host.docker.internal:8080/tools/exec/", netguard.INTERNAL),
        ("http://metadata.google.internal/computeMetadata/v1/", netguard.INTERNAL),
        ("http://litellm:8000/health", netguard.INTERNAL),
        ("http://kloudchat-db/", netguard.INTERNAL),
        ("http://0x7f000001/", netguard.INTERNAL),
        ("http://2130706433/", netguard.INTERNAL),
        ("http://api.localhost/", netguard.INTERNAL),
        ("http://printer.local/", netguard.INTERNAL),
        ("http://intranet.example/", netguard.INTERNAL),
        ("http://mixed.example/", netguard.INTERNAL),
        ("http://mapped.example/", netguard.INTERNAL),
        ("http://cgnat.example/", netguard.INTERNAL),
        ("http://linklocal.example/", netguard.INTERNAL),
        ("http://nowhere.example/", netguard.UNRESOLVED),
    ],
)
async def test_everything_else_is_refused_with_a_reason(url, reason):
    assert await netguard.refusal(url, resolve=_resolve) == reason


@pytest.mark.asyncio
async def test_the_fetch_tool_refuses_before_any_request_is_made(monkeypatch):
    async def never(*args):
        raise AssertionError("the shim was called")

    monkeypatch.setattr(builtin, "_scrape", never)
    result = await builtin.fetch_url({"url": "http://127.0.0.1:8100/api/health"})
    assert result.failed
    assert netguard.INTERNAL in result.content


@pytest.mark.asyncio
async def test_the_shim_is_not_asked_for_an_internal_page_from_search_results(monkeypatch):
    """`_scrape` itself checks: search hits and research picks go through it without
    the tool wrapper."""

    class _Client:
        def __init__(self, *args, **kwargs):
            raise AssertionError("an HTTP client was opened")

    monkeypatch.setattr(builtin.httpx, "AsyncClient", _Client)
    assert await builtin._scrape("http://gateway/tools/fetch", "http://10.0.0.1/") == ""


class _Db:
    def __init__(self, row):
        self.row = row

    async def get(self, model, item_id):
        return self.row if item_id == self.row.id else None


class _User:
    id = "owner"


@pytest.mark.asyncio
async def test_project_knowledge_from_a_url_refuses_an_internal_address():
    row = Project(id="p1", user_id="owner", name="연구")
    with pytest.raises(workspace.HTTPException) as caught:
        await workspace.add_project_url(
            "p1", KnowledgeUrl(url="http://host.docker.internal:8080/health"), _User(), _Db(row)
        )
    assert caught.value.status_code == 400
    assert caught.value.detail == netguard.INTERNAL
