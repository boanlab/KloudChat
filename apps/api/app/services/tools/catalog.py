"""Admin-managed MCP connector catalogue; env sealed, gateway key only for matching tool address."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.services import settings_store, tools_gateway

log = logging.getLogger(__name__)

#: Placeholder → feature address.
_URL_VARS = {
    "TOOLS_RESEARCH_URL": "research",
    "TOOLS_STT_URL": "stt",
    "TOOLS_SEARCH_URL": "search",
    "TOOLS_FETCH_URL": "fetch",
    "TOOLS_EXEC_URL": "exec",
}
TRANSPORTS = ("stdio", "http", "sse")
KINDS = ("chat", "report", "slides")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,59}")
_PLACEHOLDER = re.compile(r"\$\{(TOOLS_[A-Z]+_URL)\}")
_TTL = 10.0

#: Cached entries by slug, plus load time.
_CACHE: dict[str, dict[str, Any]] = {}
_READ_AT = 0.0


def _entry(row) -> dict[str, Any]:
    return {
        "id": row.id, "slug": row.slug, "name": row.name, "description": row.description,
        "category": row.category, "transport": row.transport, "endpoint": row.endpoint,
        "env": dict(row.env or {}), "required_env": list(row.required_env or []),
        "auth": row.auth or "none",
        "tools_key_for": (row.tools_key_var, row.tools_key_feature)
        if row.tools_key_var and row.tools_key_feature else None,
        "kinds": list(row.kinds or ["chat"]), "enabled": bool(row.enabled),
        "listed": bool(getattr(row, "listed", True)),
        "position": int(row.position or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def use(entries: list[dict[str, Any]]) -> None:
    """Install given entries."""
    global _READ_AT
    _CACHE.clear()
    for entry in entries:
        _CACHE[entry["slug"]] = dict(entry)
    _READ_AT = time.monotonic()


async def refresh(db=None) -> None:
    """Reload from the table."""
    from sqlmodel import col, select

    from app.models.workspace import ConnectorCatalogEntry

    async def read(session) -> list:
        return list((await session.exec(select(ConnectorCatalogEntry).order_by(
            col(ConnectorCatalogEntry.position), col(ConnectorCatalogEntry.name)))).all())

    try:
        if db is not None:
            rows = await read(db)
        else:
            from app.core.db import SessionLocal

            async with SessionLocal() as session:
                rows = await read(session)
    except Exception as exc:  # noqa: BLE001 — a missing table lists nothing, keeps what was read
        log.info("connector catalogue unreadable: %s", type(exc).__name__)
        return
    use([_entry(r) for r in rows])


async def entries(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    """Catalogue in order; reloaded when stale."""
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    rows = sorted(_CACHE.values(), key=lambda e: (e.get("position", 0), e["name"]))
    return [e for e in rows if e.get("enabled", True) or not enabled_only]


async def entry(slug: str) -> dict[str, Any] | None:
    """Entry by slug, disabled included."""
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    return _CACHE.get(slug)


def catalog_entry(slug: str) -> dict[str, Any] | None:
    """Entry by slug from the cache."""
    return _CACHE.get(slug)


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
        found = await entry(connector.slug)
        if found and found.get("endpoint"):
            return await resolve_urls(str(found["endpoint"]))
    return await resolve_urls(connector.endpoint or "")


async def connector_env(connector: Any) -> dict[str, str]:
    """Resolved env, plus gateway key only when the URL var resolves to that tool's address."""
    env = await resolve_env(connector.env)
    env.pop("TOOLS_GATEWAY_KEY", None)
    found = await entry(connector.slug) if getattr(connector, "official", False) else None
    target = (found or {}).get("tools_key_for")
    if target:
        variable, feature = target
        key = await tools_gateway.key_for(env.get(variable, ""), (feature,))
        if key:
            env["TOOLS_GATEWAY_KEY"] = key
    return env


async def resolve_env(env: dict[str, str] | None) -> dict[str, str]:
    """Decrypted env with feature placeholders substituted."""
    out: dict[str, str] = {}
    for key, value in settings_store.decrypt_env(env).items():
        out[key] = await resolve_urls(value)
    return out


class EntryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _one_line(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit]


def clean(payload: dict[str, Any], *, previous_env: dict[str, str] | None = None
          ) -> dict[str, Any]:
    """Validated admin-form fields; env sealed, `null` keeps stored value."""
    slug = str(payload.get("slug") or "").strip().lower()
    if not _SLUG.fullmatch(slug):
        raise EntryError("catalog_slug")
    name = _one_line(payload.get("name"), 80)
    if not name:
        raise EntryError("catalog_name")
    transport = str(payload.get("transport") or "")
    if transport not in TRANSPORTS:
        raise EntryError("catalog_transport")
    endpoint = str(payload.get("endpoint") or "").strip()
    if not endpoint or len(endpoint) > 500 or "\n" in endpoint:
        raise EntryError("catalog_endpoint")
    if transport != "stdio" and not re.match(r"(?:https?://|\$\{TOOLS_[A-Z]+_URL\})", endpoint):
        raise EntryError("catalog_endpoint")
    if any(m not in _URL_VARS for m in _PLACEHOLDER.findall(endpoint)):
        raise EntryError("catalog_placeholder")
    kinds = [str(k) for k in payload.get("kinds") or ["chat"]]
    if not kinds or any(k not in KINDS for k in kinds):
        raise EntryError("catalog_kinds")
    env_in = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    if len(env_in) > 12:
        raise EntryError("catalog_env")
    env: dict[str, str] = {}
    previous = previous_env or {}
    for key, value in env_in.items():
        key = str(key)
        if not _ENV_KEY.fullmatch(key) or key == "TOOLS_GATEWAY_KEY":
            raise EntryError("catalog_env")
        if value is None:
            if key in previous:
                env[key] = previous[key]
            continue
        text = str(value)
        if len(text) > 500 or "\n" in text:
            raise EntryError("catalog_env")
        env[key] = settings_store.encrypt_secret(text)
    required = []
    for row in payload.get("required_env") or []:
        row = row if isinstance(row, dict) else {}
        key = str(row.get("key") or "")
        if not _ENV_KEY.fullmatch(key):
            raise EntryError("catalog_required_env")
        required.append({"key": key, "label": _one_line(row.get("label") or key, 40),
                         "hint": _one_line(row.get("hint"), 120),
                         "secret": bool(row.get("secret"))})
    var = str(payload.get("tools_key_var") or "")
    feature = str(payload.get("tools_key_feature") or "")
    if bool(var) != bool(feature):
        raise EntryError("catalog_tools_key")
    if var and (var not in env and var not in previous or feature not in tools_gateway.FEATURES):
        raise EntryError("catalog_tools_key")
    return {
        "slug": slug, "name": name, "description": _one_line(payload.get("description"), 300),
        "category": _one_line(payload.get("category"), 30), "transport": transport,
        "endpoint": endpoint, "env": env or None, "required_env": required,
        "auth": "none", "tools_key_var": var, "tools_key_feature": feature,
        "kinds": list(dict.fromkeys(kinds)), "enabled": bool(payload.get("enabled", True)),
        "listed": bool(payload.get("listed", True)),
        "position": max(0, min(int(payload.get("position") or 0), 999)),
    }


def admin_wire(entry: dict[str, Any]) -> dict[str, Any]:
    """Admin view of an entry; secret values masked."""
    shown = {}
    for key, value in settings_store.decrypt_env(entry.get("env")).items():
        shown[key] = value if _PLACEHOLDER.fullmatch(value) else None
    target = entry.get("tools_key_for") or ("", "")
    return {
        "id": entry.get("id"), "slug": entry["slug"], "name": entry["name"],
        "description": entry.get("description", ""), "category": entry.get("category", ""),
        "transport": entry["transport"], "endpoint": entry.get("endpoint", ""),
        "env": shown, "requiredEnv": list(entry.get("required_env") or []),
        "toolsKeyVar": target[0], "toolsKeyFeature": target[1],
        "kinds": list(entry.get("kinds") or []), "enabled": bool(entry.get("enabled", True)),
        "listed": bool(entry.get("listed", True)),
        "position": int(entry.get("position") or 0), "updatedAt": entry.get("updated_at"),
    }


__all__ = ["KINDS", "TRANSPORTS", "EntryError", "admin_wire", "catalog_entry", "clean",
           "connector_env", "effective_endpoint", "entries", "entry", "refresh",
           "resolve_env", "resolve_urls", "use"]
