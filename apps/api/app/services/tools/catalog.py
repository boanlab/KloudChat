"""Installable MCP servers: the catalogue administrators keep (관리 › 커넥터 카탈로그).

The entries are rows (`ConnectorCatalogEntry`), seeded by migration 0062 with the three the code
once listed (time, youtube, deep-research). Members install from them; an administrator adds,
edits and removes them, a stdio command included (only an administrator may put a command on
the server). Stdio scripts under `/srv/mcp` run via `uv run --script` (PEP 723 inline deps);
reference servers need `npx`. Every enabled tool ships its schema on every turn, so the list is
kept short.

An entry's `env` values are stored sealed (`settings_store.encrypt_env`) and never sent back to
a browser: an administrator sees each key, and a `${TOOLS_*_URL}` placeholder as written.
The tools gateway key goes into a catalogue connector's process only as `TOOLS_GATEWAY_KEY`,
and only while the variable the entry names (`tools_key_var`) resolves to the address of the
tool it names (`tools_key_feature`), so an override cannot point it elsewhere.

Readers in a turn are synchronous on a copy held in this process (`entries`), reloaded from the
table at most every few seconds and at once after an administrator's change (`refresh`).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.services import settings_store, tools_gateway

log = logging.getLogger(__name__)

#: Placeholder → feature address from the admin screen.
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

#: The entries as this process last read them, by slug, and when.
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
        "position": int(row.position or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def use(entries: list[dict[str, Any]]) -> None:
    """Hold these entries (rows already read, or a test's)."""
    global _READ_AT
    _CACHE.clear()
    for entry in entries:
        _CACHE[entry["slug"]] = dict(entry)
    _READ_AT = time.monotonic()


async def refresh(db=None) -> None:
    """Read the table again (with `db`, or a session of its own)."""
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
    """The catalogue in order, read again when the copy is older than a few seconds."""
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    rows = sorted(_CACHE.values(), key=lambda e: (e.get("position", 0), e["name"]))
    return [e for e in rows if e.get("enabled", True) or not enabled_only]


async def entry(slug: str) -> dict[str, Any] | None:
    """One entry by slug (an installed connector's), disabled ones included."""
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    return _CACHE.get(slug)


def catalog_entry(slug: str) -> dict[str, Any] | None:
    """One entry by slug from the copy held now (no read)."""
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
    """A connector's resolved environment, plus the gateway key for a catalogue server
    that calls a tool backend itself (`tools_key_for`: the URL variable it calls and
    the tool that address must be).

    The key is added only when that variable resolves to that tool's configured
    address, so a supplied override cannot point it elsewhere."""
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
    """A connector's environment opened (`settings_store.decrypt_env`) with feature-address
    placeholders substituted."""
    out: dict[str, str] = {}
    for key, value in settings_store.decrypt_env(env).items():
        out[key] = await resolve_urls(value)
    return out


# ── what an administrator may store ──────────────────────────────────────


class EntryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _one_line(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit]


def clean(payload: dict[str, Any], *, previous_env: dict[str, str] | None = None
          ) -> dict[str, Any]:
    """An entry's fields from the admin form, each checked; `env` values sealed. A value sent
    as `null` keeps the stored one (the form never sees a stored value)."""
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
        "position": max(0, min(int(payload.get("position") or 0), 999)),
    }


def admin_wire(entry: dict[str, Any]) -> dict[str, Any]:
    """An entry as the admin screen shows it: every env key, a placeholder value as written,
    any other value only as 「set」 — never the value itself."""
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
        "position": int(entry.get("position") or 0), "updatedAt": entry.get("updated_at"),
    }


__all__ = ["KINDS", "TRANSPORTS", "EntryError", "admin_wire", "catalog_entry", "clean",
           "connector_env", "effective_endpoint", "entries", "entry", "refresh",
           "resolve_env", "resolve_urls", "use"]
