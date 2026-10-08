"""Installable MCP servers.

Stdio scripts under `/srv/mcp` run via `uv run --script` (PEP 723 inline deps);
reference servers need `npx`. Every enabled tool ships its schema on every turn,
so the list is kept short. `required_env` values are stored on the connector row
and never serialised back to the browser.
"""

from __future__ import annotations

from typing import Any

from app.services import settings_store, tools_gateway

_MCP_DIR = "/srv/mcp"

# Reference servers import `McpError`, renamed in SDK 2.x.
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
        # The gateway key goes in as TOOLS_GATEWAY_KEY, only while WHISPER_URL is the stt address.
        "tools_key_for": ("WHISPER_URL", "stt"),
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


#: Placeholder → feature address from the admin screen.
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
    """The address to call: catalogue connectors re-read the catalogue, others their stored row."""
    if getattr(connector, "official", False):
        entry = catalog_entry(connector.slug)
        if entry and entry.get("endpoint"):
            return await resolve_urls(str(entry["endpoint"]))
    return await resolve_urls(connector.endpoint or "")


async def connector_env(connector: Any) -> dict[str, str]:
    """A connector's resolved environment, plus the gateway key for a catalogue server
    that calls a tool backend itself (`tools_key_for`: the URL variable it calls and
    the tool that address must be).

    The key is added only when that variable resolves to that tool's configured
    address, so a supplied override cannot point it elsewhere."""
    env = await resolve_env(connector.env)
    env.pop("TOOLS_GATEWAY_KEY", None)
    entry = catalog_entry(connector.slug) if getattr(connector, "official", False) else None
    target = (entry or {}).get("tools_key_for")
    if target:
        variable, feature = target
        key = await tools_gateway.key_for(env.get(variable, ""), (feature,))
        if key:
            env["TOOLS_GATEWAY_KEY"] = key
    return env


async def resolve_env(env: dict[str, str] | None) -> dict[str, str]:
    """A connector's environment opened (`settings_store.decrypt_env`) with feature-address
    placeholders substituted."""
    out: dict[str, str] = {}
    for key, value in settings_store.decrypt_env(env).items():
        out[key] = await resolve_urls(value)
    return out


def catalog_entry(slug: str) -> dict[str, Any] | None:
    return next((e for e in CATALOG if e["slug"] == slug), None)
