"""기본 제공 connectors: shared servers of an administrator, installed for every active person.

A person's copy is a reference row (`origin_id`): switchable, not removable. Its tools come from
the shared server's own tool rows while that server is connected, else from the server, asked once
per process; a server that does not answer leaves the copy in error with 「다시 연결」.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User, UserStatus, utcnow
from app.models.workspace import (
    Connector,
    ConnectorStatus,
    ConnectorTool,
    Transport,
    Visibility,
)
from app.services import mcp, settings_store
from app.services.tools import catalog

log = logging.getLogger(__name__)

#: One provisioning run at a time across replicas.
LOCK_KEY = 7_331_069
#: Seconds a server gets to list its tools.
DISCOVERY_SEC = 60.0
#: shared server id → (address, tool specs) asked of the server in this process.
_DISCOVERED: dict[str, tuple[str, list[dict[str, Any]]]] = {}

_MCP_SDK = "'mcp<2'"
#: Shipped 기본 제공 servers, shared by the first administrator as ordinary rows.
SHIPPED: tuple[dict[str, Any], ...] = (
    {"slug": "time", "name": "시간",
     "description": "현재 시각과 타임존 변환. '오늘'을 모델이 지어내지 않게 합니다.",
     "category": "기본", "transport": "stdio",
     "endpoint": f"uvx --with {_MCP_SDK} mcp-server-time --local-timezone=Asia/Seoul",
     "env": None, "tools_key_var": "", "tools_key_feature": "",
     "kinds": ["chat", "report", "slides"]},
    {"slug": "youtube", "name": "YouTube 전사",
     "description": "영상의 자막을 가져오고, 없으면 음성을 전사합니다.",
     "category": "미디어", "transport": "stdio", "endpoint": "uv run --script /srv/mcp/youtube.py",
     "env": {"WHISPER_URL": "${TOOLS_STT_URL}"}, "tools_key_var": "WHISPER_URL",
     "tools_key_feature": "stt", "kinds": ["chat", "report"]},
    {"slug": "deep-research", "name": "심층 리서치",
     "description": "arXiv·웹을 반복 탐색하는 ReAct 에이전트. 수 분에서 수십 분 걸립니다.",
     "category": "연구", "transport": "http", "endpoint": "${TOOLS_RESEARCH_URL}/mcp",
     "env": None, "tools_key_var": "", "tools_key_feature": "", "kinds": ["chat", "report"]},
)

Spec = dict[str, Any]


async def _lock(db: AsyncSession) -> None:
    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})


async def _owned(db: AsyncSession, source: dict[str, Any]) -> list[Spec] | None:
    """Tools of the shared server's own row, when it is connected."""
    row = await db.get(Connector, source["id"])
    if row is None or row.status is not ConnectorStatus.connected:
        return None
    rows = (await db.exec(
        select(ConnectorTool).where(ConnectorTool.connector_id == row.id))).all()
    return [{"name": r.name, "description": r.description, "parameters": r.parameters,
             "read_only": r.read_only} for r in rows] or None


async def _asked(source: dict[str, Any]) -> list[Spec] | None:
    """Tools the server lists, once per address per process."""
    probe = Connector(owner_id=source["owner_id"], name=source["name"], slug=source["slug"],
                      transport=Transport(source["transport"]), endpoint=source["endpoint"],
                      env=source.get("env"), origin_id=source["id"])
    try:
        endpoint = await catalog.effective_endpoint(probe)
    except catalog.NotRunnable:
        return None
    cached = _DISCOVERED.get(source["id"])
    if cached and cached[0] == endpoint:
        return cached[1]
    env = mcp.substitute(await catalog.connector_env(probe), user_id=source["owner_id"],
                         user_email="")
    try:
        async with asyncio.timeout(DISCOVERY_SEC):
            listed = await mcp.list_tools(source["transport"], endpoint, env)
    except Exception as exc:  # noqa: BLE001 — a dead server leaves copies in error
        log.info("default connector %s: no tool list: %s", source["slug"], type(exc).__name__)
        return None
    specs = [{"name": spec["name"], "description": spec.get("description") or "",
              "parameters": spec.get("inputSchema") or {"type": "object", "properties": {}},
              "read_only": not catalog.looks_like_write(spec["name"])}
             for spec in listed if spec.get("name")]
    _DISCOVERED[source["id"]] = (endpoint, specs)
    return specs


async def _specs(db: AsyncSession, source: dict[str, Any]) -> list[Spec] | None:
    return await _owned(db, source) or await _asked(source)


def reference(db: AsyncSession, user: User, source: dict[str, Any],
              specs: list[Spec] | None, env: dict[str, str] | None = None) -> Connector:
    """The person's reference row to a shared server; caller commits. Address, shared env and
    gateway key stay on the shared row; only the person's own credentials are kept here."""
    now = utcnow()
    connector = Connector(
        owner_id=user.id, name=source["name"], slug=source["slug"],
        description=source.get("description", ""), category=source.get("category", ""),
        transport=Transport(source["transport"]), endpoint="",
        env=settings_store.encrypt_env(env) if env else None,
        auth_type=source.get("auth", "none"), kinds=source.get("kinds", ["chat"]),
        origin_id=source["id"],
        status=ConnectorStatus.connected if specs is not None else ConnectorStatus.error,
        error=None if specs is not None else "도구 목록을 받지 못했습니다. 다시 연결해 보세요.",
        last_sync_at=now,
    )
    db.add(connector)
    for spec in specs or []:
        db.add(ConnectorTool(connector_id=connector.id, name=spec["name"],
                             description=spec["description"], parameters=spec["parameters"],
                             read_only=spec["read_only"], enabled=spec["read_only"]))
    return connector


async def _wanted() -> list[dict[str, Any]]:
    return [e for e in await catalog.sources() if catalog.provided(e)]


async def provide(db: AsyncSession, user: User) -> int:
    """Default servers this person lacks, installed; caller commits."""
    wanted = [e for e in await _wanted() if e["owner_id"] != user.id]
    if not wanted or user.status is not UserStatus.active:
        return 0
    have = set((await db.exec(select(Connector.origin_id).where(
        Connector.owner_id == user.id))).all())
    made = 0
    for entry in wanted:
        if entry["id"] not in have:
            reference(db, user, entry, await _specs(db, entry))
            made += 1
    return made


async def provide_everyone(db: AsyncSession) -> int:
    """Default servers installed for every active person lacking them; commits."""
    wanted = await _wanted()
    if not wanted:
        return 0
    await _lock(db)
    people = list((await db.exec(select(User).where(User.status == UserStatus.active))).all())
    ids = [e["id"] for e in wanted]
    have = {(owner, origin) for owner, origin in (await db.exec(
        select(Connector.owner_id, Connector.origin_id).where(
            col(Connector.origin_id).in_(ids)))).all()}
    made = 0
    for entry in wanted:
        missing = [p for p in people
                   if p.id != entry["owner_id"] and (p.id, entry["id"]) not in have]
        if not missing:
            continue
        specs = await _specs(db, entry)
        for person in missing:
            reference(db, person, entry, specs)
        made += len(missing)
    await db.commit()
    if made:
        log.info("default connectors installed: %d", made)
    return made


async def connect_owners(db: AsyncSession) -> int:
    """A default server its sharer has not connected yet (just seeded) gets its tools from the
    server; caller commits."""
    made = 0
    for entry in await _wanted():
        row = await db.get(Connector, entry["id"])
        if row is None or row.status is ConnectorStatus.connected:
            continue
        specs = await _asked(entry)
        if specs is None:
            continue
        for spec in specs:
            db.add(ConnectorTool(connector_id=row.id, name=spec["name"],
                                 description=spec["description"], parameters=spec["parameters"],
                                 read_only=spec["read_only"], enabled=spec["read_only"]))
        row.status, row.error, row.last_sync_at = ConnectorStatus.connected, None, utcnow()
        db.add(row)
        made += 1
    return made


async def provide_at_startup(session_factory) -> int:
    """Shared defaults connected for their sharer; copies missing since the last start
    installed."""
    async with session_factory() as db:
        await catalog.refresh(db)
        if await connect_owners(db):
            await db.commit()
        return await provide_everyone(db)


async def seed_shipped(db: AsyncSession, owner: User) -> int:
    """The shipped 기본 제공 servers, shared by the first administrator; caller commits."""
    made = 0
    for spec in SHIPPED:
        db.add(Connector(
            owner_id=owner.id, name=spec["name"], slug=spec["slug"],
            description=spec["description"], category=spec["category"],
            transport=Transport(spec["transport"]), endpoint=spec["endpoint"],
            env=settings_store.encrypt_env(spec["env"]) if spec["env"] else None,
            kinds=list(spec["kinds"]), visibility=Visibility.org, by_default=True,
            tools_key_var=spec["tools_key_var"], tools_key_feature=spec["tools_key_feature"],
            status=ConnectorStatus.disconnected))
        made += 1
    return made


async def kept(connector: Connector) -> bool:
    """Whether a person's copy is a 기본 제공 one, which they cannot remove."""
    if not connector.origin_id:
        return False
    return catalog.provided(await catalog.source(connector.origin_id))


__all__ = ["SHIPPED", "connect_owners", "kept", "provide", "provide_at_startup",
           "provide_everyone", "reference", "seed_shipped"]
