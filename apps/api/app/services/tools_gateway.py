"""The KloudChat-LLM gateway key on requests to the tool backends.

The gateway answers `/tools/*` only with `X-Tools-Key` (`TOOLS_GATEWAY_KEY` on
both sides). Every client that talks to a tool backend is built by `client()`:
its request hook adds the key when the request URL lies under one of the
configured tool addresses (`settings_store.tools_config`) and removes it
otherwise, on every hop, so the key never goes to a fetched page, a third-party
MCP server or a redirect target.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store

HEADER = "X-Tools-Key"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    scheme = url.scheme.lower()
    return scheme, url.host.lower(), url.port or _DEFAULT_PORTS.get(scheme)


def under(url: str | httpx.URL, base: str) -> bool:
    """Whether `url` is `base` or a path beneath it, on the same scheme, host and port."""
    if not base:
        return False
    try:
        target = httpx.URL(str(url))
        root = httpx.URL(base.strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    if not root.host or _origin(target) != _origin(root):
        return False
    prefix = root.path.rstrip("/")
    return target.path == prefix or target.path.startswith(prefix + "/")


FEATURES = ("search", "fetch", "exec", "research", "stt", "index", "ocr")


async def is_tools_url(url: str | httpx.URL, features: tuple[str, ...] = FEATURES) -> bool:
    backends = await settings_store.tools_config()
    return any(under(url, backends.get(feature)) for feature in features)


async def key_for(url: str | httpx.URL, features: tuple[str, ...] = FEATURES) -> str:
    """The key when `url` is one of `features`' tool addresses, else ""."""
    key = settings.tools_gateway_key.strip()
    if key and await is_tools_url(url, features):
        return key
    return ""


def client(*, features: tuple[str, ...] = FEATURES, **kwargs: Any) -> httpx.AsyncClient:
    """`httpx.AsyncClient` that carries the gateway key to the tool backends only.

    `features` narrows which tool addresses get it: the MCP client, which also
    calls servers people add themselves, passes `("research",)`."""

    async def attach(request: httpx.Request) -> None:
        # Whatever a caller set is dropped; only the configured key, only to a tool.
        request.headers.pop(HEADER, None)
        key = await key_for(request.url, features)
        if key:
            request.headers[HEADER] = key

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [attach, *hooks.get("request", [])]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
