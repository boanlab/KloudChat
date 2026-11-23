"""MCP servers: a person's own, shared ones in the store, tool sync, per-tool toggles."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func
from sqlmodel import col, delete, select

from app.core.deps import CurrentUser, DbSession
from app.models.user import User, UserRole, utcnow
from app.models.workspace import (
    Connector,
    ConnectorStatus,
    ConnectorTool,
    Transport,
    Visibility,
)
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


async def _installs(db: DbSession, source_id: str) -> int:
    return (await db.exec(select(func.count()).select_from(Connector).where(
        Connector.origin_id == source_id))).one()


async def _owner_name(db: DbSession, owner_id: str) -> str:
    owner = await db.get(User, owner_id)
    return (owner.name or owner.email.split("@")[0]) if owner else ""


async def _out(db: DbSession, user: User, connector: Connector) -> ConnectorOut:
    out = ConnectorOut.of(connector, await _tools_of(db, connector.id))
    out.by_default = await connector_defaults.kept(connector)
    if connector.origin_id:
        origin = await catalog.source(connector.origin_id)
        out.endpoint = ""
        if origin is None or not origin.get("enabled", True):
            out.origin_gone = True
        else:
            out.name, out.description = origin["name"], origin.get("description", "")
            out.category, out.kinds = origin.get("category", ""), list(origin.get("kinds") or [])
            out.transport = Transport(origin["transport"])
            out.official = bool(origin["owner_is_admin"])
            out.origin_owner_name = await _owner_name(db, origin["owner_id"])
    elif connector.visibility is Visibility.org:
        out.official = user.role is UserRole.admin
        out.installs = await _installs(db, connector.id)
        out.shared_env = catalog.share_wire({"env": connector.env or {}})["env"]
    return out


async def _removable(connector: Connector) -> None:
    if await connector_defaults.kept(connector):
        # 기본 제공: switched off, never removed
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="connector_default")


@router.get("/store")
async def get_store(user: CurrentUser, db: DbSession):
    """Servers others share, with whether this person has one; unlisted ones only when installed
    (an administrator sees all)."""
    await catalog.refresh(db)
    mine = {c.origin_id for c in (await db.exec(
        select(Connector).where(Connector.owner_id == user.id))).all() if c.origin_id}
    out = []
    for entry in await catalog.sources():
        if entry["owner_id"] == user.id:
            continue
        installed = entry["id"] in mine
        if not entry["listed"] and not installed and user.role is not UserRole.admin:
            continue
        out.append({
            "id": entry["id"], "slug": entry["slug"], "name": entry["name"],
            "description": entry["description"], "category": entry["category"],
            "transport": entry["transport"], "auth": entry.get("auth", "none"),
            "kinds": entry.get("kinds", ["chat"]), "official": entry["owner_is_admin"],
            "ownerName": await _owner_name(db, entry["owner_id"]),
            "installed": installed, "byDefault": catalog.provided(entry),
            "listed": entry["listed"],
            "requiredEnv": [
                {"key": f["key"], "label": f.get("label", f["key"]), "hint": f.get("hint", ""),
                 "secret": bool(f.get("secret"))}
                for f in entry.get("required_env", [])
            ],
        })
    return out


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(user: CurrentUser, db: DbSession):
    await catalog.refresh(db)
    rows = (
        await db.exec(
            select(Connector).where(Connector.owner_id == user.id).order_by(col(Connector.name))
        )
    ).all()
    return [await _out(db, user, c) for c in rows]


def _supplied(source: dict[str, Any], supplied: dict[str, str]) -> dict[str, str]:
    """A person sets only the credentials the shared server asks for: any other variable reaches
    the server process (an index URL for `uvx`, a preload) and runs code on the API host."""
    asked = {str(f.get("key")) for f in source.get("required_env", [])}
    if extra := sorted(set(supplied) - asked):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"env_not_allowed:{','.join(extra)}")
    return {k: v for k, v in supplied.items() if v.strip()}


@router.post("/install/{source_id}", response_model=ConnectorOut,
             status_code=status.HTTP_201_CREATED)
async def install(
    source_id: str,
    user: CurrentUser,
    db: DbSession,
    payload: InstallRequest | None = None,
):
    await catalog.refresh(db)
    source = await catalog.source(source_id)
    if source is None or not source.get("enabled", True) or source["owner_id"] == user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_connector")

    supplied = _supplied(source, (payload.env if payload else None) or {})
    missing = [f["key"] for f in source.get("required_env", [])
               if not supplied.get(f["key"], "").strip()]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"missing_env:{','.join(missing)}",
        )

    existing = (
        await db.exec(
            select(Connector).where(Connector.owner_id == user.id,
                                    Connector.origin_id == source_id)
        )
    ).first()
    if existing:
        return await _out(db, user, existing)

    connector = connector_defaults.reference(db, user, source, [], supplied)
    # tools are discovered below; unreachable server still installed, error on row
    connector.status = ConnectorStatus.disconnected
    await db.commit()
    await db.refresh(connector)
    await _sync(db, user, connector)
    return await _out(db, user, connector)


def _stdio_refused() -> None:
    """A command runs on the API server: only an administrator registers one."""
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="stdio_requires_admin")


async def _remote_settable(endpoint: str | None) -> None:
    """A member's own http/sse server: a public address only (the API would call into the
    deployment's network for them); checked again at connect time."""
    if reason := await catalog.endpoint_refusal(endpoint or ""):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=reason)


def _entry_error(exc: catalog.EntryError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.code)


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def add_custom(payload: ConnectorIn, user: CurrentUser, db: DbSession):
    if payload.transport is Transport.stdio and user.role is not UserRole.admin:
        _stdio_refused()
    if payload.transport is not Transport.stdio and user.role is not UserRole.admin:
        await _remote_settable(payload.endpoint)
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
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    await _sync(db, user, connector)
    return await _out(db, user, connector)


async def _sync(db: DbSession, user: User, connector: Connector) -> None:
    """Stored tools reconciled with server; `enabled` flags kept (no re-grant of revoked)."""
    is_admin = user.role is UserRole.admin
    try:
        await catalog.check_runnable(connector, owner_is_admin=is_admin)
        env = mcp.substitute(
            await catalog.connector_env(connector, owner_is_admin=is_admin),
            user_id=user.id, user_email=user.email,
        )
        endpoint = await catalog.effective_endpoint(connector)
        transport = await catalog.effective_transport(connector)
        guarded = await catalog.guarded(connector, owner_is_admin=is_admin)
        discovered = await mcp.list_tools(transport, endpoint, env,
                                          **({"public_only": True} if guarded else {}))
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
    await catalog.refresh(db)
    await _sync(db, user, connector)
    await db.refresh(connector)
    return await _out(db, user, connector)


def _shares(fields: dict[str, Any]) -> bool:
    return any(k in fields for k in ("visibility", "required_env", "tools_key_var",
                                     "tools_key_feature", "by_default", "listed"))


async def _patch_reference(connector: Connector, fields: dict[str, Any]) -> None:
    """A reference row keeps only the person's switches and their own credentials."""
    if _shares(fields) or {"endpoint", "name", "description"} & set(fields):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="catalogue_endpoint")
    if fields.get("env") is not None:
        source = await catalog.source(connector.origin_id)
        _supplied(source or {}, fields["env"])


async def _patch_share(db: DbSession, user: User, connector: Connector,
                       fields: dict[str, Any]) -> dict[str, Any]:
    """Sharing settings of the person's own server; returns the columns to set."""
    is_admin = user.role is UserRole.admin
    out: dict[str, Any] = {}
    try:
        if "required_env" in fields:
            out["required_env"] = catalog.clean_required(
                [r.model_dump() if hasattr(r, "model_dump") else r
                 for r in fields["required_env"] or []])
        if "visibility" in fields:
            out["visibility"] = Visibility(fields["visibility"])
        if not is_admin and any(k in fields for k in
                                ("tools_key_var", "tools_key_feature", "by_default", "listed")):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="shared_admin_only")
        env = {**dict(connector.env or {}), **settings_store.encrypt_env(fields.get("env") or {})}
        if "tools_key_var" in fields or "tools_key_feature" in fields:
            out["tools_key_var"], out["tools_key_feature"] = catalog.clean_tools_key(
                fields.get("tools_key_var", connector.tools_key_var),
                fields.get("tools_key_feature", connector.tools_key_feature),
                settings_store.decrypt_env(env))
        if "listed" in fields:
            out["listed"] = bool(fields["listed"])
        if "by_default" in fields:
            out["by_default"] = bool(fields["by_default"])
    except catalog.EntryError as exc:
        raise _entry_error(exc) from exc
    shared = out.get("visibility", connector.visibility) is Visibility.org
    required = out.get("required_env", connector.required_env)
    if out.get("by_default") and (not shared or required):
        # a default is installed for everyone; nobody is there to type each person's credentials
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="catalog_default_env")
    if out.get("by_default") is None and connector.by_default and required:
        # credentials added to a default server: it is no longer installed for everyone
        out["by_default"] = False
    if not shared:
        # withdrawing a server stops the references of others at once; the screen says so first
        out["by_default"] = False
    elif connector.transport is Transport.stdio and not is_admin:
        _stdio_refused()
    elif not is_admin:
        await _remote_settable(fields.get("endpoint", connector.endpoint))
    return out


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def patch(connector_id: str, payload: ConnectorPatch, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    patch_fields = payload.model_dump(exclude_unset=True)
    is_admin = user.role is UserRole.admin
    if connector.origin_id:
        await _patch_reference(connector, patch_fields)
    elif not is_admin and ("endpoint" in patch_fields or "env" in patch_fields):
        if connector.transport is Transport.stdio:
            _stdio_refused()
        if "endpoint" in patch_fields:
            await _remote_settable(patch_fields["endpoint"])
    if patch_fields.get("installed") is False:
        await _removable(connector)
    changes: dict[str, Any] = {}
    if not connector.origin_id and _shares(patch_fields):
        changes = await _patch_share(db, user, connector, patch_fields)
    if is_admin and not connector.origin_id:
        shared = changes.get("visibility", connector.visibility) is Visibility.org
        if shared and ("endpoint" in patch_fields or "visibility" in changes):
            try:
                catalog.endpoint_form(connector.transport.value,
                                      patch_fields.get("endpoint", connector.endpoint))
            except catalog.EntryError as exc:
                raise _entry_error(exc) from exc
    for key in ("visibility", "required_env", "tools_key_var", "tools_key_feature",
                "by_default", "listed"):
        patch_fields.pop(key, None)
    # env merged, not replaced: keeps what is already set
    if (env := patch_fields.pop("env", None)) is not None:
        connector.env = {**(connector.env or {}), **settings_store.encrypt_env(env)}
    for field, value in {**patch_fields, **changes}.items():
        setattr(connector, field, value)
    connector.updated_at = utcnow()
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    await catalog.refresh(db)
    return await _out(db, user, connector)


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
    return await _out(db, user, connector)


async def _in_use(db: DbSession, row: Connector) -> bool:
    """A shared server others installed: withdrawn first, then removed."""
    return (not row.origin_id and row.visibility is Visibility.org
            and await _installs(db, row.id) > 0)


@router.post("/delete")
async def uninstall_many(payload: BulkDelete, user: CurrentUser, db: DbSession):
    """Bulk uninstall; 기본 제공 copies and servers others use stay."""
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
           if not await connector_defaults.kept(row) and not await _in_use(db, row)]
    if not ids:
        return {"deleted": 0, "kept": len(rows)}
    await db.exec(delete(ConnectorTool).where(col(ConnectorTool.connector_id).in_(ids)))
    await db.exec(delete(Connector).where(col(Connector.id).in_(ids)))
    await db.commit()
    await catalog.refresh(db)
    return {"deleted": len(ids), "kept": len(rows) - len(ids)}


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(connector_id: str, user: CurrentUser, db: DbSession):
    connector = await _own(db, user, connector_id)
    await _removable(connector)
    if await _in_use(db, connector):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="connector_in_use")
    await db.exec(delete(ConnectorTool).where(ConnectorTool.connector_id == connector.id))
    await db.delete(connector)
    await db.commit()
    await catalog.refresh(db)
