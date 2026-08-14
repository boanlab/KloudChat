"""Installable MCP servers.

The `mcp/*.py` scripts are standard MCP over stdio and run unmodified. They
declare their dependencies inline with `uv run --script` (PEP 723), so the API
image needs `uv` on PATH and the scripts mounted at `/srv/mcp`; the reference
servers from the MCP project are npm packages and need `npx`. See the
Dockerfile.

**Entry criteria.** A server belongs here once it has been started against a
real credential and had its tool count checked. Every enabled tool ships its
full schema on every turn, and model tool-choice degrades well before twenty of
them — so the size of this list is the constraint, not the length of the
candidate set.

`required_env` is what an operator or user must supply before the server can
start. Asked for at install time and stored on the connector row, which is
never serialised back to the browser.
"""

from __future__ import annotations

from typing import Any

from app.services import settings_store

_MCP_DIR = "/srv/mcp"

# The reference servers import `McpError`, renamed to `MCPError` in SDK 2.x.
# Unbounded, uvx resolves the newest SDK and every server dies on import.
_MCP_SDK = "'mcp<2'"

CATALOG: list[dict[str, Any]] = [
    {
        "slug": "time",
        "name": "시간",
        "description": "현재 시각과 타임존 변환. '오늘'을 모델이 지어내지 않게 합니다.",
        "category": "기본",
        "transport": "stdio",
        "endpoint": f"uvx --with {_MCP_SDK} mcp-server-time --local-timezone=Asia/Seoul",
        "kinds": ["chat", "report", "slides"],
    },
    {
        "slug": "youtube",
        "name": "YouTube 전사",
        "description": "영상의 자막을 가져오고, 없으면 음성을 전사합니다.",
        "category": "미디어",
        "transport": "stdio",
        "endpoint": f"uv run --script {_MCP_DIR}/youtube.py",
        "env": {"WHISPER_URL": "${TOOLS_STT_URL}"},
        "kinds": ["chat", "report"],
    },
    {
        "slug": "deep-research",
        "name": "심층 리서치",
        "description": "arXiv·웹을 반복 탐색하는 ReAct 에이전트. 수 분에서 수십 분 걸립니다.",
        "category": "연구",
        "transport": "http",
        "endpoint": "${TOOLS_RESEARCH_URL}/mcp",
        "kinds": ["chat", "report"],
    },
]


#: Placeholder → feature address from the admin screen. A hard-coded address
#: would leave installed connectors pointing at the old backend.
_URL_VARS = {
    "TOOLS_RESEARCH_URL": "research",
    "TOOLS_STT_URL": "stt",
    "TOOLS_SEARCH_URL": "search",
    "TOOLS_FETCH_URL": "fetch",
    "TOOLS_EXEC_URL": "exec",
}


async def resolve_urls(text: str) -> str:
    """Substitutes `${TOOLS_*_URL}` in a string with the current settings."""
    if "${" not in text:
        return text
    backends = await settings_store.tools_config()
    for name, feature in _URL_VARS.items():
        token = "${" + name + "}"
        if token in text:
            text = text.replace(token, backends.get(feature).rstrip("/"))
    return text


async def effective_endpoint(connector: Any) -> str:
    """The address the connector will actually call.

    Catalogue connectors re-read the catalogue rather than their stored row, so
    a backend move carries them with it. A self-registered server keeps the
    address the user typed.
    """
    if getattr(connector, "official", False):
        entry = catalog_entry(connector.slug)
        if entry and entry.get("endpoint"):
            return await resolve_urls(str(entry["endpoint"]))
    return await resolve_urls(connector.endpoint or "")


async def resolve_env(env: dict[str, str] | None) -> dict[str, str]:
    """Substitutes feature-address placeholders in a connector's environment."""
    out: dict[str, str] = {}
    for key, value in (env or {}).items():
        out[key] = await resolve_urls(str(value))
    return out


def catalog_entry(slug: str) -> dict[str, Any] | None:
    return next((e for e in CATALOG if e["slug"] == slug), None)


def required_env(slug: str) -> list[dict[str, Any]]:
    entry = catalog_entry(slug)
    return list((entry or {}).get("required_env") or [])
