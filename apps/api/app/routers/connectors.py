"""MCP connectors: catalogue install, tool sync, per-tool toggles; `admin_router` = catalogue."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field
from sqlmodel import col, delete, select

from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.user import AuditEvent, User, UserRole, utcnow
from app.models.workspace import (
    Connector,
    ConnectorCatalogEntry,
    ConnectorStatus,
    ConnectorTool,
    Transport,
)
from app.schemas.auth import Wire
from app.schemas.workspace import (
    BulkDelete,
    ConnectorIn,
    ConnectorOut,
    ConnectorPatch,
    InstallRequest,
    ToolToggle,
)
from app.services import connector_defaults, mcp, settings_store
from app.services.tools import catalog

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
    out = ConnectorOut.of(connector, await _tools_of(db, connector.id))
    out.by_default = await connector_defaults.kept(connector.slug, official=connector.official)
    return out


async def _removable(connector: Connector) -> None:
    if await connector_defaults.kept(connector.slug, official=connector.official):
        # 기본 제공: switched off, never removed
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="connector_default")


@router.get("/catalog")
async def get_catalog(user: CurrentUser, db: DbSession):
    """Admin catalogue with installed marks; unlisted entries only if installed."""
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
            "byDefault": catalog.provided(entry),
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
        for entry in await catalog.entries()
        if entry.get("listed", True) or entry["slug"] in installed
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
    entry = await catalog.entry(slug)
    if entry is None or not entry.get("enabled", True):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_connector")

    supplied = (payload.env if payload else None) or {}
    missing = [
        field["key"]
        for field in entry.get("required_env", [])
        if not supplied.get(field["key"], "").strip()
    ]
    if missing:
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
        # supplied credential over catalogue placeholder; unsealed then resealed with user's
        env=settings_store.encrypt_env({**settings_store.decrypt_env(entry.get("env")),
                                        **supplied}),
        auth_type=entry.get("auth", "none"),
        kinds=entry.get("kinds", ["chat"]),
        official=True,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    # best-effort: unreachable server still installed, error on row
    await _sync(db, user, connector)
    return await _out(db, connector)


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def add_custom(payload: ConnectorIn, user: CurrentUser, db: DbSession):
    if payload.transport is Transport.stdio and user.role is not UserRole.admin:
        # stdio = command on API server → admin only
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="stdio_requires_admin")
    connector = Connector(
        owner_id=user.id,
        name=payload.name,
        slug=payload.name.lower().replace(" ", "-")[:60],
        description=payload.description,
        category=payload.category,
        transport=payload.transport,
        endpoint=payload.endpoint,
        env=settings_store.encrypt_env(payload.env),
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
    """Stored tools reconciled with server; `enabled` flags kept (no re-grant of revoked)."""
    env = mcp.substitute(
        await catalog.connector_env(connector), user_id=user.id, user_email=user.email
    )
    try:
        endpoint = await catalog.effective_endpoint(connector)
        discovered = await mcp.list_tools(connector.transport.value, endpoint, env)
    except Exception as exc:  # noqa: BLE001 — dead server is a status, not a 500
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
        read_only = not catalog.looks_like_write(name)
        row = existing.get(name)
        if row is None:
            db.add(
                ConnectorTool(
                    connector_id=connector.id,
                    name=name,
                    description=spec.get("description") or "",
                    parameters=spec.get("inputSchema") or {"type": "object", "properties": {}},
                    read_only=read_only,
                    # write tools start off
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
    if patch_fields.get("installed") is False:
        await _removable(connector)
    # env merged, not replaced: keeps catalogue defaults
    if (env := patch_fields.pop("env", None)) is not None:
        connector.env = {**(connector.env or {}), **settings_store.encrypt_env(env)}
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
    """Bulk uninstall; 기본 제공 copies stay (`kept`)."""
    if not payload.ids:
        return {"deleted": 0, "kept": 0}
    rows = (
        await db.exec(
            select(Connector).where(
                col(Connector.id).in_(payload.ids), Connector.owner_id == user.id
            )
        )
    ).all()
    ids = [row.id for row in rows
           if not await connector_defaults.kept(row.slug, official=row.official)]
    if not ids:
        return {"deleted": 0, "kept": len(rows)}
    await db.exec(delete(ConnectorTool).where(col(ConnectorTool.connector_id).in_(ids)))
    await db.exec(delete(Connector).where(col(Connector.id).in_(ids)))
    await db.commit()
    return {"deleted": len(ids), "kept": len(rows) - len(ids)}


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(connector_id: str, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    await _removable(connector)
    await db.exec(delete(ConnectorTool).where(ConnectorTool.connector_id == connector.id))
    await db.delete(connector)
    await db.commit()


admin_router = APIRouter(prefix="/admin/connector-catalog", tags=["connectors"])


class CatalogEntryIn(Wire):
    slug: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=30)
    transport: str = Field(max_length=10)
    endpoint: str = Field(min_length=1, max_length=500)
    #: null keeps stored value (form never sees it)
    env: dict[str, str | None] = Field(default_factory=dict)
    required_env: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    tools_key_var: str = Field(default="", max_length=60)
    tools_key_feature: str = Field(default="", max_length=20)
    kinds: list[str] = Field(default_factory=lambda: ["chat"], max_length=3)
    enabled: bool = True
    #: False = installable but not offered
    listed: bool = True
    #: 기본 제공: installed for every active person
    by_default: bool = False
    position: int = Field(default=0, ge=0, le=999)


def _audit(db: DbSession, request: Request, admin: User, action: str, slug: str) -> None:
    db.add(AuditEvent(actor_id=admin.id, action=action, target=f"connector:{slug[:60]}",
                      ip=client_ip(request),
                      user_agent=request.headers.get("User-Agent", "")[:400]))


def _fields(payload: CatalogEntryIn, previous: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        return catalog.clean(payload.model_dump(), previous_env=previous)
    except catalog.EntryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=exc.code) from exc


async def _admin_list(db: DbSession) -> list[dict[str, Any]]:
    await catalog.refresh(db)
    return [catalog.admin_wire(e) for e in await catalog.entries(enabled_only=False)]


async def _saved(db: DbSession, slug: str) -> dict[str, Any]:
    """Catalogue after a save; a 기본 제공 entry installed for whoever lacks it."""
    entries = await _admin_list(db)
    saved = await catalog.entry(slug)
    provided = (await connector_defaults.provide_everyone(db, [saved])
                if catalog.provided(saved) else 0)
    return {"entries": entries, "provided": provided}


@admin_router.get("")
async def admin_catalog(admin: AdminUser, db: DbSession):
    """All entries incl. disabled; env keys only."""
    return {"entries": await _admin_list(db), "kinds": list(catalog.KINDS),
            "transports": list(catalog.TRANSPORTS),
            "features": list(catalog.tools_gateway.FEATURES),
            "placeholders": [f"${{{name}}}" for name in catalog._URL_VARS]}


@admin_router.post("", status_code=status.HTTP_201_CREATED)
async def admin_add_entry(payload: CatalogEntryIn, admin: AdminUser, db: DbSession,
                          request: Request):
    fields = _fields(payload)
    taken = (await db.exec(select(ConnectorCatalogEntry).where(
        ConnectorCatalogEntry.slug == fields["slug"]))).first()
    if taken is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="catalog_slug_taken")
    db.add(ConnectorCatalogEntry(**fields, updated_by=admin.id))
    _audit(db, request, admin, "connector.catalog.save", fields["slug"])
    await db.commit()
    return await _saved(db, fields["slug"])


@admin_router.put("/{entry_id}")
async def admin_edit_entry(entry_id: str, payload: CatalogEntryIn, admin: AdminUser,
                           db: DbSession, request: Request):
    row = await db.get(ConnectorCatalogEntry, entry_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="catalog_entry_missing")
    fields = _fields(payload, previous=dict(row.env or {}))
    if fields["slug"] != row.slug:
        # installed connectors resolve by slug
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="catalog_slug_fixed")
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_by, row.updated_at = admin.id, utcnow()
    db.add(row)
    _audit(db, request, admin, "connector.catalog.save", row.slug)
    await db.commit()
    return await _saved(db, row.slug)


@admin_router.delete("/{entry_id}")
async def admin_remove_entry(entry_id: str, admin: AdminUser, db: DbSession, request: Request):
    """Catalogue removal; installed copies keep address, lose gateway key."""
    row = await db.get(ConnectorCatalogEntry, entry_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="catalog_entry_missing")
    _audit(db, request, admin, "connector.catalog.delete", row.slug)
    await db.delete(row)
    await db.commit()
    return {"entries": await _admin_list(db)}
