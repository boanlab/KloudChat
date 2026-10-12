"""기본 제공 connectors: catalogue entries installed for every active person.

A person's copy is an ordinary installed connector: they can switch it or its tools off, not
remove it. Tools come from a connected copy synced since the entry last changed, else from
the server, asked once per process; a server that does not answer leaves the copy in error
with 「다시 연결」, as an install would.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User, UserStatus, utcnow
from app.models.workspace import (
    Connector,
    ConnectorCatalogEntry,
    ConnectorStatus,
    ConnectorTool,
    Transport,
)
from app.services import mcp, settings_store, starter
from app.services.tools import catalog

log = logging.getLogger(__name__)

#: One provisioning run at a time across replicas.
LOCK_KEY = 7_331_069
#: Seconds a server gets to list its tools.
DISCOVERY_SEC = 60.0
#: slug → (address, tool specs) asked of the server in this process.
_DISCOVERED: dict[str, tuple[str, list[dict[str, Any]]]] = {}

Spec = dict[str, Any]


async def _lock(db: AsyncSession) -> None:
    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})


def _changed_at(entry: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(entry["updated_at"]) if entry.get("updated_at") else None
    except ValueError:
        return None


async def _copied(db: AsyncSession, entry: dict[str, Any]) -> list[Spec] | None:
    """Tools of a connected copy synced since the entry last changed."""
    query = select(Connector).where(
        Connector.slug == entry["slug"],
        Connector.official == True,  # noqa: E712
        Connector.status == ConnectorStatus.connected,
    )
    changed = _changed_at(entry)
    if changed is not None:
        query = query.where(col(Connector.last_sync_at) >= changed)
    source = (await db.exec(query.order_by(col(Connector.last_sync_at).desc()).limit(1))).first()
    if source is None:
        return None
    rows = (await db.exec(
        select(ConnectorTool).where(ConnectorTool.connector_id == source.id))).all()
    return [{"name": r.name, "description": r.description, "parameters": r.parameters,
             "read_only": r.read_only} for r in rows] or None


async def _asked(entry: dict[str, Any], user: User) -> list[Spec] | None:
    """Tools the server lists, once per address per process."""
    probe = Connector(owner_id=user.id, name=entry["name"], slug=entry["slug"],
                      transport=Transport(entry["transport"]), endpoint=entry["endpoint"],
                      env=entry.get("env"), official=True)
    endpoint = await catalog.effective_endpoint(probe)
    cached = _DISCOVERED.get(entry["slug"])
    if cached and cached[0] == endpoint:
        return cached[1]
    env = mcp.substitute(await catalog.connector_env(probe), user_id=user.id,
                         user_email=user.email)
    try:
        async with asyncio.timeout(DISCOVERY_SEC):
            listed = await mcp.list_tools(entry["transport"], endpoint, env)
    except Exception as exc:  # noqa: BLE001 — a dead server leaves copies in error
        log.info("default connector %s: no tool list: %s", entry["slug"], type(exc).__name__)
        return None
    specs = [{"name": spec["name"], "description": spec.get("description") or "",
              "parameters": spec.get("inputSchema") or {"type": "object", "properties": {}},
              "read_only": not catalog.looks_like_write(spec["name"])}
             for spec in listed if spec.get("name")]
    _DISCOVERED[entry["slug"]] = (endpoint, specs)
    return specs


async def _specs(db: AsyncSession, entry: dict[str, Any], user: User) -> list[Spec] | None:
    return await _copied(db, entry) or await _asked(entry, user)


def _install(db: AsyncSession, user: User, entry: dict[str, Any],
             specs: list[Spec] | None) -> Connector:
    """The person's copy, as `POST /connectors/install` leaves it; caller commits."""
    now = utcnow()
    connector = Connector(
        owner_id=user.id, name=entry["name"], slug=entry["slug"],
        description=entry.get("description", ""), category=entry.get("category", ""),
        transport=Transport(entry["transport"]), endpoint=entry["endpoint"],
        env=settings_store.encrypt_env(settings_store.decrypt_env(entry.get("env"))),
        auth_type=entry.get("auth", "none"), kinds=entry.get("kinds", ["chat"]), official=True,
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


async def _wanted(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [e for e in (entries if entries is not None else await catalog.entries())
            if catalog.provided(e)]


async def provide(db: AsyncSession, user: User) -> int:
    """Default entries this person lacks, installed; caller commits."""
    wanted = await _wanted(None)
    if not wanted or user.status is not UserStatus.active:
        return 0
    have = set((await db.exec(select(Connector.slug).where(
        Connector.owner_id == user.id))).all())
    made = 0
    for entry in wanted:
        if entry["slug"] not in have:
            _install(db, user, entry, await _specs(db, entry, user))
            made += 1
    return made


async def provide_everyone(db: AsyncSession,
                           entries: list[dict[str, Any]] | None = None) -> int:
    """Default entries installed for every active person lacking them; commits."""
    wanted = await _wanted(entries)
    if not wanted:
        return 0
    await _lock(db)
    people = list((await db.exec(select(User).where(User.status == UserStatus.active))).all())
    slugs = [e["slug"] for e in wanted]
    have = {(owner, slug) for owner, slug in (await db.exec(
        select(Connector.owner_id, Connector.slug).where(col(Connector.slug).in_(slugs)))).all()}
    made = 0
    for entry in wanted:
        missing = [p for p in people if (p.id, entry["slug"]) not in have]
        if not missing:
            continue
        specs = await _specs(db, entry, missing[0])
        for person in missing:
            _install(db, person, entry, specs)
        made += len(missing)
    await db.commit()
    if made:
        log.info("default connectors installed: %d", made)
    return made


async def provide_at_startup(session_factory) -> int:
    """Shipped entries claimed; copies missing since the last start installed."""
    async with session_factory() as db:
        owner = await starter.catalog_owner_id(db)
        if owner is not None:
            await claim(db, owner)
            await db.commit()
        await catalog.refresh(db)
        return await provide_everyone(db)


async def claim(db: AsyncSession, owner_id: str) -> None:
    """Entries no one has saved (shipped ones), attributed to the catalogue owner; caller
    commits."""
    await db.exec(update(ConnectorCatalogEntry)
                  .where(col(ConnectorCatalogEntry.updated_by).is_(None))
                  .values(updated_by=owner_id))


async def kept(slug: str, *, official: bool) -> bool:
    """Whether a person's copy is a 기본 제공 one, which they cannot remove."""
    return official and catalog.provided(await catalog.entry(slug))


__all__ = ["claim", "kept", "provide", "provide_at_startup", "provide_everyone"]
