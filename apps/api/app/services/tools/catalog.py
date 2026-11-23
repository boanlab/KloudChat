"""Shared MCP servers and the trust rule a connector runs under.

A shared server is a `Connector` row with `visibility = org` and no `origin_id`. A person who
installs it gets a reference row: the address, the shared env and the gateway key are read from
the shared row at run time, never copied. The shared env and the gateway key are used only while
the sharer is an administrator now; a member's shared server runs with each person's own
credentials, and only to public addresses.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.services import netguard, settings_store, tools_gateway

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
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,59}")
_PLACEHOLDER = re.compile(r"\$\{(TOOLS_[A-Z]+_URL)\}")
_TTL = 10.0

#: Shared servers by id, plus load time.
_CACHE: dict[str, dict[str, Any]] = {}
_READ_AT = 0.0


def _entry(row, admins: set[str]) -> dict[str, Any]:
    return {
        "id": row.id, "slug": row.slug, "name": row.name, "description": row.description,
        "category": row.category, "transport": str(getattr(row.transport, "value", row.transport)),
        "endpoint": row.endpoint, "env": dict(row.env or {}),
        "required_env": list(row.required_env or []), "auth": row.auth_type or "none",
        "tools_key_for": (row.tools_key_var, row.tools_key_feature)
        if row.tools_key_var and row.tools_key_feature else None,
        "kinds": list(row.kinds or ["chat"]), "enabled": bool(row.enabled),
        "listed": bool(row.listed), "by_default": bool(row.by_default),
        "owner_id": row.owner_id, "owner_is_admin": row.owner_id in admins,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def use(entries: list[dict[str, Any]]) -> None:
    """Install given shared servers."""
    global _READ_AT
    _CACHE.clear()
    for entry in entries:
        _CACHE[entry["id"]] = dict(entry)
    _READ_AT = time.monotonic()


async def refresh(db=None) -> None:
    """Reload the shared servers."""
    from sqlmodel import col, select

    from app.models.user import User, UserRole
    from app.models.workspace import Connector, Visibility

    async def read(session) -> list[dict[str, Any]]:
        rows = list((await session.exec(select(Connector).where(
            Connector.visibility == Visibility.org, col(Connector.origin_id).is_(None))
            .order_by(col(Connector.name)))).all())
        owners = {r.owner_id for r in rows}
        admins = set((await session.exec(select(User.id).where(
            col(User.id).in_(owners), User.role == UserRole.admin))).all()) if owners else set()
        return [_entry(r, admins) for r in rows]

    try:
        if db is not None:
            found = await read(db)
        else:
            from app.core.db import SessionLocal

            async with SessionLocal() as session:
                found = await read(session)
    except Exception as exc:  # noqa: BLE001 — an unreadable table keeps what was read
        log.info("shared connectors unreadable: %s", type(exc).__name__)
        return
    use(found)


async def sources(*, enabled_only: bool = True) -> list[dict[str, Any]]:
    """Shared servers by name; reloaded when stale."""
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    rows = sorted(_CACHE.values(), key=lambda e: e["name"])
    return [e for e in rows if e.get("enabled", True) or not enabled_only]


async def source(source_id: str | None) -> dict[str, Any] | None:
    """Shared server by id, disabled included."""
    if not source_id:
        return None
    if time.monotonic() - _READ_AT > _TTL:
        await refresh()
    return _CACHE.get(source_id)


def provided(entry: dict[str, Any] | None) -> bool:
    """Whether a shared server is 기본 제공 now: marked, enabled, an administrator's, and asking
    for no credentials."""
    return bool(entry and entry.get("by_default") and entry.get("enabled", True)
                and entry.get("owner_is_admin") and not entry.get("required_env"))


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


class NotRunnable(RuntimeError):
    pass


#: What a reference says when the shared server is gone or switched off.
GONE = "공유된 서버가 더 이상 열려 있지 않아 실행하지 않았습니다."


async def _origin(connector: Any) -> dict[str, Any] | None:
    """The shared server a reference row points at; raises when it is no longer there."""
    if not getattr(connector, "origin_id", None):
        return None
    found = await source(connector.origin_id)
    if found is None or not found.get("enabled", True):
        raise NotRunnable(GONE)
    return found


async def trusted(connector: Any, *, owner_is_admin: bool) -> bool:
    """Whether the connector may run a command or reach an internal address: a reference row
    follows its sharer's role now; any other row its owner's."""
    origin = await _origin(connector)
    return bool(origin["owner_is_admin"]) if origin else owner_is_admin


async def effective_transport(connector: Any) -> str:
    """How to reach the server: a reference row follows the shared server's transport."""
    origin = await _origin(connector)
    return str((origin or {}).get("transport")
               or getattr(connector.transport, "value", connector.transport))


async def effective_endpoint(connector: Any) -> str:
    """The address to call: a reference row reads the shared server's, others their own."""
    origin = await _origin(connector)
    return await resolve_urls(str((origin or {}).get("endpoint") or connector.endpoint or ""))


async def connector_env(connector: Any, *, owner_is_admin: bool = False) -> dict[str, str]:
    """Resolved env: the shared server's (only while its sharer is an administrator), then the
    person's own credentials; the gateway key only when the URL var resolves to that tool."""
    origin = await _origin(connector)
    env: dict[str, str] = {}
    if origin and origin["owner_is_admin"]:
        env.update(await resolve_env(origin["env"]))
    mine = await resolve_env(connector.env)
    if origin:
        asked = {str(f.get("key")) for f in origin.get("required_env") or []}
        mine = {k: v for k, v in mine.items() if k in asked}
    env.update(mine)
    env.pop("TOOLS_GATEWAY_KEY", None)
    target = (origin or {}).get("tools_key_for") if (origin or {}).get("owner_is_admin") else None
    if target is None and origin is None and owner_is_admin \
            and getattr(connector, "tools_key_var", ""):
        target = (connector.tools_key_var, connector.tools_key_feature)
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


#: Member's own remote connector aimed at the deployment's network.
INTERNAL_ENDPOINT = "내부 네트워크 주소의 MCP 서버는 관리자만 등록할 수 있습니다."


async def guarded(connector: Any, *, owner_is_admin: bool) -> bool:
    """Whether a remote connector may reach only public addresses: anything not run under an
    administrator's trust (a member's own, or a reference to a member's shared server)."""
    return await effective_transport(connector) != "stdio" \
        and not await trusted(connector, owner_is_admin=owner_is_admin)


async def endpoint_refusal(endpoint: str) -> str | None:
    """Why a member's remote endpoint is refused (internal, a tool-address placeholder, or not
    http(s)), or None; a name that does not resolve now is left to the connect-time check."""
    if "${" in endpoint:
        return INTERNAL_ENDPOINT
    reason = await netguard.refusal(endpoint)
    if reason == netguard.UNRESOLVED:
        return None
    return INTERNAL_ENDPOINT if reason == netguard.INTERNAL else reason


async def check_runnable(connector: Any, *, owner_is_admin: bool) -> None:
    """A command runs only under an administrator's trust (their own row, or a reference to
    their shared server); a member's remote one only to a public address; a reference to a
    server no longer shared is refused."""
    if await guarded(connector, owner_is_admin=owner_is_admin):
        origin = await _origin(connector)
        if "${" in str((origin or {}).get("endpoint") or getattr(connector, "endpoint", None)
                       or ""):
            raise NotRunnable(INTERNAL_ENDPOINT)
        if reason := await endpoint_refusal(await effective_endpoint(connector)):
            raise NotRunnable(reason)
        return
    if await effective_transport(connector) != "stdio":
        return
    if await trusted(connector, owner_is_admin=owner_is_admin):
        return
    raise NotRunnable("명령으로 실행하는 서버는 관리자만 등록할 수 있어 실행하지 않았습니다.")


#: Tool-name words that mark a write tool (off until opted in).
_WRITE_HINTS = (
    "create",
    "update",
    "delete",
    "write",
    "post",
    "send",
    "push",
    "publish",
    "upload",
    "insert",
    "remove",
    "add",
    "set",
    "edit",
    "move",
    "rename",
)


def looks_like_write(name: str) -> bool:
    """Name hints at a write."""
    lowered = name.lower()
    return any(hint in lowered for hint in _WRITE_HINTS)


class EntryError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _one_line(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit]


def endpoint_form(transport: str, endpoint: str) -> None:
    """An administrator's server address: a command, or an http(s) URL / tool-address
    placeholder; raises `EntryError`."""
    endpoint = (endpoint or "").strip()
    if not endpoint or len(endpoint) > 500 or "\n" in endpoint:
        raise EntryError("catalog_endpoint")
    if transport not in TRANSPORTS:
        raise EntryError("catalog_transport")
    if transport != "stdio" and not re.match(r"(?:https?://|\$\{TOOLS_[A-Z]+_URL\})", endpoint):
        raise EntryError("catalog_endpoint")
    if any(m not in _URL_VARS for m in _PLACEHOLDER.findall(endpoint)):
        raise EntryError("catalog_placeholder")


def clean_required(raw: Any) -> list[dict[str, Any]]:
    """Credentials each person who installs the server enters."""
    required = []
    for row in raw or []:
        row = row if isinstance(row, dict) else {}
        key = str(row.get("key") or "")
        if not _ENV_KEY.fullmatch(key) or key == "TOOLS_GATEWAY_KEY":
            raise EntryError("catalog_required_env")
        required.append({"key": key, "label": _one_line(row.get("label") or key, 40),
                         "hint": _one_line(row.get("hint"), 120),
                         "secret": bool(row.get("secret"))})
    if len(required) > 8:
        raise EntryError("catalog_required_env")
    return required


def clean_tools_key(var: str, feature: str, env: dict[str, str]) -> tuple[str, str]:
    """The env variable holding a tool address, and which tool's gateway key goes with it."""
    var, feature = str(var or ""), str(feature or "")
    if bool(var) != bool(feature):
        raise EntryError("catalog_tools_key")
    if var and (var not in env or feature not in tools_gateway.FEATURES):
        raise EntryError("catalog_tools_key")
    return var, feature


def share_wire(entry: dict[str, Any]) -> dict[str, Any]:
    """Owner/admin view of a shared server's settings; secret values masked."""
    shown = {}
    for key, value in settings_store.decrypt_env(entry.get("env")).items():
        shown[key] = value if _PLACEHOLDER.fullmatch(value) else None
    target = entry.get("tools_key_for") or ("", "")
    return {"env": shown, "toolsKeyVar": target[0], "toolsKeyFeature": target[1]}


__all__ = ["GONE", "INTERNAL_ENDPOINT", "KINDS", "TRANSPORTS", "EntryError", "NotRunnable",
           "check_runnable", "clean_required", "clean_tools_key", "connector_env",
           "effective_endpoint", "effective_transport", "endpoint_form", "endpoint_refusal",
           "guarded", "looks_like_write", "provided", "refresh", "resolve_env", "resolve_urls",
           "share_wire", "source", "sources", "trusted", "use"]
