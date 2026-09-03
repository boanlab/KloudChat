"""MCP connectors: install from a catalogue, sync tools, toggle per tool.

Installing makes a server's tools reachable from chat. Syncing asks the server
what it exposes rather than trusting a hardcoded list.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from sqlmodel import col, delete, select

from app.core.deps import CurrentUser, DbSession
from app.models.user import User, utcnow
from app.models.workspace import Connector, ConnectorStatus, ConnectorTool, Transport
from app.schemas.workspace import (
    BulkDelete,
    ConnectorIn,
    ConnectorOut,
    ConnectorPatch,
    InstallRequest,
    ToolToggle,
)
from app.services import mcp
from app.services.tools import catalog
from app.services.tools.catalog import CATALOG, catalog_entry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["connectors"])


async def _own(db: DbSession, user: User, connector_id: str) -> Connector:
    row = await db.get(Connector, connector_id)
    if row is None or row.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


async def _tools_of(db: DbSession, connector_id: str) -> list[ConnectorTool]:
    rows = await db.exec(
        select(ConnectorTool)
        .where(ConnectorTool.connector_id == connector_id)
        .order_by(col(ConnectorTool.name))
    )
    return list(rows.all())


async def _out(db: DbSession, connector: Connector) -> ConnectorOut:
    return ConnectorOut.of(connector, await _tools_of(db, connector.id))


@router.get("/catalog")
async def get_catalog(user: CurrentUser, db: DbSession):
    """Installable servers, with the ones this user already has marked."""
    installed = {
        c.slug
        for c in (await db.exec(select(Connector).where(Connector.owner_id == user.id))).all()
    }
    return [
        {
            "slug": entry["slug"],
            "name": entry["name"],
            "description": entry["description"],
            "category": entry["category"],
            "transport": entry["transport"],
            "auth": entry.get("auth", "none"),
            "kinds": entry.get("kinds", ["chat"]),
            "official": True,
            "installed": entry["slug"] in installed,
            # What must be supplied before the server can start. Labels and
            # hints travel with it, so the dialog needs no per-server code.
            "requiredEnv": [
                {
                    "key": field["key"],
                    "label": field.get("label", field["key"]),
                    "hint": field.get("hint", ""),
                    "secret": bool(field.get("secret")),
                }
                for field in entry.get("required_env", [])
            ],
        }
        for entry in CATALOG
    ]


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(user: CurrentUser, db: DbSession):
    rows = (
        await db.exec(
            select(Connector).where(Connector.owner_id == user.id).order_by(col(Connector.name))
        )
    ).all()
    return [await _out(db, c) for c in rows]


@router.post("/install/{slug}", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def install(
    slug: str,
    user: CurrentUser,
    db: DbSession,
    payload: InstallRequest | None = None,
):
    entry = catalog_entry(slug)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_connector")

    supplied = (payload.env if payload else None) or {}
    missing = [
        field["key"]
        for field in entry.get("required_env", [])
        if not supplied.get(field["key"], "").strip()
    ]
    if missing:
        # Without the credential the server would fail on every call.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"missing_env:{','.join(missing)}",
        )

    existing = (
        await db.exec(
            select(Connector).where(Connector.owner_id == user.id, Connector.slug == slug)
        )
    ).first()
    if existing:
        return await _out(db, existing)

    connector = Connector(
        owner_id=user.id,
        name=entry["name"],
        slug=slug,
        description=entry["description"],
        category=entry["category"],
        transport=Transport(entry["transport"]),
        endpoint=entry["endpoint"],
        # Catalogue defaults first: a supplied credential wins over a
        # placeholder of the same name.
        env={**(entry.get("env") or {}), **supplied},
        auth_type=entry.get("auth", "none"),
        kinds=entry.get("kinds", ["chat"]),
        official=True,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    # Best-effort: an unreachable server is still installed, with the error on
    # the row.
    await _sync(db, user, connector)
    return await _out(db, connector)


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def add_custom(payload: ConnectorIn, user: CurrentUser, db: DbSession):
    connector = Connector(
        owner_id=user.id,
        name=payload.name,
        slug=payload.name.lower().replace(" ", "-")[:60],
        description=payload.description,
        category=payload.category,
        transport=payload.transport,
        endpoint=payload.endpoint,
        env=payload.env,
        auth_type=payload.auth,
        kinds=payload.kinds or ["chat"],
        official=False,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    await _sync(db, user, connector)
    return await _out(db, connector)


async def _sync(db: DbSession, user: User, connector: Connector) -> None:
    """Asks the server for its tools and reconciles the stored list.

    Disappeared tools are removed, new ones added with their read/write default.
    Existing `enabled` flags survive: a sync must not re-grant a revoked
    permission.
    """
    env = mcp.substitute(
        await catalog.resolve_env(connector.env), user_id=user.id, user_email=user.email
    )
    try:
        endpoint = await catalog.effective_endpoint(connector)
        discovered = await mcp.list_tools(connector.transport.value, endpoint, env)
    except Exception as exc:  # noqa: BLE001 — a dead server is a status, not a 500
        log.info("connector %s sync failed: %s", connector.slug, exc)
        connector.status = ConnectorStatus.error
        connector.error = str(exc)[:500]
        connector.last_sync_at = utcnow()
        db.add(connector)
        await db.commit()
        return

    existing = {t.name: t for t in await _tools_of(db, connector.id)}
    seen: set[str] = set()
    for spec in discovered:
        name = spec.get("name")
        if not name:
            continue
        seen.add(name)
        # Pessimistic heuristic: a name suggesting mutation counts as a write.
        read_only = not _looks_like_write(name)
        row = existing.get(name)
        if row is None:
            db.add(
                ConnectorTool(
                    connector_id=connector.id,
                    name=name,
                    description=spec.get("description") or "",
                    parameters=spec.get("inputSchema") or {"type": "object", "properties": {}},
                    read_only=read_only,
                    # Write tools start off. Enabling one is an explicit act.
                    enabled=read_only,
                )
            )
        else:
            row.description = spec.get("description") or row.description
            row.parameters = spec.get("inputSchema") or row.parameters
            row.read_only = read_only
            db.add(row)

    for name, row in existing.items():
        if name not in seen:
            await db.delete(row)

    connector.status = ConnectorStatus.connected
    connector.error = None
    connector.last_sync_at = utcnow()
    db.add(connector)
    await db.commit()


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


def _looks_like_write(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _WRITE_HINTS)


@router.post("/{connector_id}/sync", response_model=ConnectorOut)
async def sync(connector_id: str, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    await _sync(db, user, connector)
    await db.refresh(connector)
    return await _out(db, connector)


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def patch(connector_id: str, payload: ConnectorPatch, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    patch_fields = payload.model_dump(exclude_unset=True)
    # Merged, not replaced: a rotation touches one field, and a whole-map write
    # would drop the catalogue defaults beside it — which
    # are what tell the server where to look.
    if (env := patch_fields.pop("env", None)) is not None:
        connector.env = {**(connector.env or {}), **env}
    for field, value in patch_fields.items():
        setattr(connector, field, value)
    connector.updated_at = utcnow()
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return await _out(db, connector)


@router.post("/{connector_id}/tools/{tool_name}", response_model=ConnectorOut)
async def toggle_tool(
    connector_id: str, tool_name: str, payload: ToolToggle, user: CurrentUser, db: DbSession
):
    connector = await _own(db, user, connector_id)
    row = (
        await db.exec(
            select(ConnectorTool).where(
                ConnectorTool.connector_id == connector.id, ConnectorTool.name == tool_name
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tool_not_found")
    row.enabled = payload.enabled
    db.add(row)
    await db.commit()
    return await _out(db, connector)


@router.post("/delete")
async def uninstall_many(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Several at once. Credentials go with the rows they belong to."""
    if not payload.ids:
        return {"deleted": 0}
    rows = (
        await db.exec(
            select(Connector).where(
                col(Connector.id).in_(payload.ids), Connector.user_id == user.id
            )
        )
    ).all()
    if not rows:
        return {"deleted": 0}
    ids = [row.id for row in rows]
    await db.exec(delete(ConnectorTool).where(col(ConnectorTool.connector_id).in_(ids)))
    await db.exec(delete(Connector).where(col(Connector.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids)}


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(connector_id: str, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    await db.exec(delete(ConnectorTool).where(ConnectorTool.connector_id == connector.id))
    await db.delete(connector)
    await db.commit()
