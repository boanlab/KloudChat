"""X-Tools-Key on tool-backend requests only; stripped on every other hop."""

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
    """Whether url is base or beneath it, same scheme/host/port."""
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
    """Key when url is a tool address of features, else ""."""
    key = settings.tools_gateway_key.strip()
    if key and await is_tools_url(url, features):
        return key
    return ""


def client(*, features: tuple[str, ...] = FEATURES, **kwargs: Any) -> httpx.AsyncClient:
    """AsyncClient sending the gateway key to tool backends only (features narrows which)."""

    async def attach(request: httpx.Request) -> None:
        # caller-set key dropped; configured key to tools only
        request.headers.pop(HEADER, None)
        key = await key_for(request.url, features)
        if key:
            request.headers[HEADER] = key

    hooks = dict(kwargs.pop("event_hooks", None) or {})
    hooks["request"] = [attach, *hooks.get("request", [])]
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
