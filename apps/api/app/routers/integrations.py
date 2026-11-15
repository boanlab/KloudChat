"""Google and Slack connections: 설정 › 연결 for people, the provider setup for the
administrator, and the OAuth callback the provider sends people back to."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlmodel import col, func, select

from app.core.config import settings
from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession
from app.models.integrations import IntegrationAccount, SlackWorkspace
from app.models.user import User
from app.models.workspace import StoredFile
from app.schemas.auth import Wire
from app.schemas.workspace import FileOut
from app.services import settings_store
from app.services.integrations import accounts, actions, config, drive
from app.services.integrations import tools as _tools  # noqa: F401 — registers the writes
from app.services.integrations.google import Google
from app.services.integrations.http import ProviderError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])
admin_router = APIRouter(prefix="/admin/integrations", tags=["integrations"])


def _provider(provider: str) -> str:
    if provider not in config.PROVIDERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_provider")
    return provider


def _refused(exc: ProviderError) -> HTTPException:
    # a lapsed grant is not a lapsed app session: never 401, which signs the person out
    code = exc.status if 400 <= exc.status < 600 and exc.status != 401 else (
        409 if exc.status == 401 else 502)
    return HTTPException(status_code=code, detail=exc.code)


def _browser_cookie(provider: str) -> str:
    return f"kc_connect_{provider}"


def _granted_features(row, cfg: config.ProviderConfig) -> list[str]:
    return [f for f in cfg.features if accounts.granted(row, f)]


@router.get("")
async def list_integrations(user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    """Each provider the administrator turned on, with this person's connection."""
    out: list[dict[str, Any]] = []
    for provider in config.PROVIDERS:
        cfg = await config.get(provider)
        if not cfg.usable:
            continue
        row = await accounts.account(db, user.id, provider)
        connection = None
        if row is not None and row.status != "pending":
            connection = {
                "status": row.status,
                "label": row.label,
                "features": _granted_features(row, cfg),
                "connectedAt": row.created_at,
                "lastUsedAt": row.last_used_at,
                "prefs": row.prefs or {},
            }
        out.append({"provider": provider, "features": list(cfg.features),
                    "connection": connection})
    return out


@router.post("/{provider}/connect")
async def connect(provider: str, response: Response, user: CurrentUser,
                  db: DbSession) -> dict[str, str]:
    """Where to send the browser to grant access; a cookie ties the return to this browser."""
    provider = _provider(provider)
    nonce = secrets.token_urlsafe(32)
    try:
        url = await accounts.start(db, user.id, provider, nonce)
    except ProviderError as exc:
        raise _refused(exc) from None
    response.set_cookie(_browser_cookie(provider), nonce, httponly=True,
                        secure=settings.cookie_secure, samesite="lax",
                        max_age=int(accounts.STATE_TTL.total_seconds()),
                        path=f"{settings.api_prefix}/integrations/{provider}")
    return {"url": url}


def _back(path: str, **query: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(f"{path}?{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{provider}/callback")
async def callback(provider: str, request: Request, db: DbSession, code: str = "",
                   state: str = "", error: str = ""):
    """The provider sends the browser back here; no bearer token arrives, the `state` names
    the person (it was stored hashed when they pressed 연결)."""
    provider = _provider(provider)
    settings_path = "/settings/connections"
    if error:
        # the person said no, or the provider refused (a Slack workspace that does not let
        # members install apps): the waiting state is dropped
        await accounts.abandon(db, provider, state)
        return _back(settings_path, error="denied", provider=provider)
    try:
        await accounts.finish(db, provider, state, code,
                              request.cookies.get(_browser_cookie(provider), ""))
    except ProviderError as exc:
        log.info("integration callback refused: %s %s", provider, exc.code)
        back = _back(settings_path, error=exc.code, provider=provider)
    else:
        back = _back(settings_path, connected=provider)
    back.delete_cookie(_browser_cookie(provider),
                       path=f"{settings.api_prefix}/integrations/{provider}")
    return back


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(provider: str, request: Request, user: CurrentUser, db: DbSession):
    row = await accounts.account(db, user.id, _provider(provider))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_connected")
    await accounts.disconnect(db, row, actor_id=user.id, ip=client_ip(request),
                              user_agent=request.headers.get("User-Agent", ""))


# ── Google Drive ─────────────────────────────────────────────────────────


async def _drive_account(db: DbSession, user, feature: str = "drive"):
    cfg = await config.get(config.GOOGLE)
    if not cfg.allows(feature):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="feature_off")
    row = await accounts.account(db, user.id, config.GOOGLE)
    if row is None or row.status != "connected":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="reconnect_required" if row is not None else "not_connected")
    if not accounts.granted(row, feature):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="scope_missing")
    return cfg, row


@router.get("/google/picker")
async def google_picker(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """What Google Picker needs in this person's browser: their own short-lived access token
    (`drive.file` and their address only: nothing else is ever granted), the browser API key
    and the Cloud project number. A picked file becomes one the app may open."""
    cfg, row = await _drive_account(db, user)
    if not cfg.picker:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="picker_unavailable")
    try:
        token = await accounts.access_token(db, row)
    except ProviderError as exc:
        raise _refused(exc) from None
    return {"accessToken": token, "apiKey": cfg.picker_key, "appId": cfg.app_id,
            "expiresAt": row.expires_at, "origin": await config.base_url()}


@router.get("/google/drive/files")
async def drive_files(user: CurrentUser, db: DbSession, q: str = "") -> list[dict[str, Any]]:
    """Files the app may open (picked before, or saved by the app), newest first."""
    _, row = await _drive_account(db, user)
    try:
        found = await Google(db, row).search(" ".join(q.split())[:100])
    except ProviderError as exc:
        raise _refused(exc) from None
    return [{"id": f.get("id"), "name": f.get("name"), "mimeType": f.get("mimeType"),
             "modifiedTime": f.get("modifiedTime"), "size": int(f.get("size") or 0),
             "url": f.get("webViewLink")} for f in found]


class DriveImport(Wire):
    file_ids: list[str] = Field(min_length=1, max_length=drive.IMPORT_MAX)
    project_id: str | None = Field(default=None, max_length=40)
    session_id: str | None = Field(default=None, max_length=40)


@router.post("/google/drive/import")
async def drive_import(payload: DriveImport, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Picked files stored as uploads (project material with `projectId`), Drive link kept."""
    _, row = await _drive_account(db, user)
    if payload.session_id:
        session = await db.get(ChatSession, payload.session_id)
        if session is None or session.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    try:
        stored, refused = await drive.bring_in(db, user, row, payload.file_ids,
                                               project_id=payload.project_id,
                                               session_id=payload.session_id)
    except ProviderError as exc:
        raise _refused(exc) from None
    return {"files": [FileOut.of(f) for f in stored], "refused": refused}


@router.post("/google/drive/files/{file_id}/sync")
async def drive_sync(file_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """A brought-in file read again from Drive, when it changed there."""
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    _, row = await _drive_account(db, user)
    try:
        stored, changed = await drive.resync(db, user, row, stored)
    except ProviderError as exc:
        raise _refused(exc) from None
    return {"file": FileOut.of(stored), "changed": changed}


@router.get("/google/drive/imports")
async def drive_imports(user: CurrentUser, db: DbSession, file_ids: str = Query(
        "", alias="fileIds", max_length=4000)) -> list[dict[str, Any]]:
    """Which of these files came from Drive (their link and last sync)."""
    ids = [i for i in file_ids.split(",") if i][:200]
    return await drive.imports_of(db, user, ids)


# ── writes waiting for the person (confirmation cards) ───────────────────


def _action_refused(exc: actions.ActionRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.code)


@router.get("/actions/{action_id}")
async def get_action(action_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    try:
        return actions.view(await actions.owned(db, user, action_id))
    except actions.ActionRefused as exc:
        raise _action_refused(exc) from None


class ActionDecision(Wire):
    #: fields the card lets the person change (`Executor.editable`)
    edits: dict[str, Any] | None = None


@router.post("/actions/{action_id}/confirm")
async def confirm_action(action_id: str, request: Request, user: CurrentUser, db: DbSession,
                         payload: ActionDecision | None = None) -> dict[str, Any]:
    """The write the card shows, sent to the provider now."""
    try:
        row = await actions.owned(db, user, action_id)
        row = await actions.confirm(db, user, row, (payload.edits if payload else None),
                                    ip=client_ip(request),
                                    user_agent=request.headers.get("User-Agent", ""))
    except actions.ActionRefused as exc:
        raise _action_refused(exc) from None
    return actions.view(row)


@router.post("/actions/{action_id}/cancel")
async def cancel_action(action_id: str, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    try:
        row = await actions.cancel(db, await actions.owned(db, user, action_id))
    except actions.ActionRefused as exc:
        raise _action_refused(exc) from None
    return actions.view(row)


# ── administrator ────────────────────────────────────────────────────────


async def _admin_view(db: DbSession, provider: str) -> dict[str, Any]:
    cfg = await config.get(provider)
    connected = (await db.exec(select(func.count()).select_from(IntegrationAccount).where(
        IntegrationAccount.provider == provider,
        IntegrationAccount.status == "connected"))).one()
    view: dict[str, Any] = {
        "provider": provider,
        "enabled": cfg.enabled,
        "clientId": cfg.client_id,
        "clientSecretSet": bool(cfg.client_secret),
        "clientSecretPreview": settings_store.preview(cfg.client_secret),
        "features": list(cfg.features),
        "allFeatures": list(config.FEATURES[provider]),
        "redirectUri": await config.redirect_uri(provider),
        "scopes": cfg.user_scopes(),
        "usable": cfg.usable,
        "connectedPeople": int(connected or 0),
    }
    if provider == config.GOOGLE:
        view.update({"pickerKey": cfg.picker_key, "appId": cfg.app_id})
    if provider == config.SLACK:
        workspaces = (await db.exec(select(SlackWorkspace).order_by(
            col(SlackWorkspace.name)))).all()
        view.update({
            "signingSecretSet": bool(cfg.signing_secret),
            "installScopes": cfg.install_scopes(),
            "eventsUrl": await config.slack_events_url(),
            "workspaces": [{"teamId": w.team_id, "name": w.name, "installedAt": w.created_at}
                           for w in workspaces],
        })
    return view


@admin_router.get("")
async def admin_integrations(admin: AdminUser, db: DbSession) -> dict[str, Any]:
    return {"providers": [await _admin_view(db, p) for p in config.PROVIDERS],
            "baseUrl": await config.base_url()}


@admin_router.get("/{provider}/accounts")
async def admin_accounts(provider: str, admin: AdminUser, db: DbSession) -> list[dict[str, Any]]:
    """Who connected (never a token): name, address, the account they linked, since when."""
    provider = _provider(provider)
    rows = (await db.exec(select(IntegrationAccount, User).join(
        User, col(User.id) == col(IntegrationAccount.user_id)).where(
        IntegrationAccount.provider == provider,
        IntegrationAccount.status != "pending").order_by(
        col(IntegrationAccount.created_at).desc()))).all()
    return [{"userId": user.id, "name": user.name, "email": user.email, "label": row.label,
             "status": row.status, "connectedAt": row.created_at, "lastUsedAt": row.last_used_at}
            for row, user in rows]


class ProviderSetup(Wire):
    enabled: bool | None = None
    client_id: str | None = Field(default=None, max_length=300)
    #: null keeps the stored secret; "" clears it
    client_secret: str | None = Field(default=None, max_length=300)
    signing_secret: str | None = Field(default=None, max_length=300)
    #: Google Picker browser key and Cloud project number
    picker_key: str | None = Field(default=None, max_length=200)
    app_id: str | None = Field(default=None, max_length=40)
    features: list[str] | None = Field(default=None, max_length=10)


@admin_router.put("/{provider}")
async def admin_save(provider: str, payload: ProviderSetup, request: Request, admin: AdminUser,
                     db: DbSession) -> dict[str, Any]:
    provider = _provider(provider)
    before = await config.get(provider)
    changed: list[str] = []
    writes: list[tuple[str, str]] = []
    if payload.enabled is not None:
        writes.append(("enabled", "1" if payload.enabled else "0"))
    for name in ("client_id", "client_secret"):
        if (value := getattr(payload, name)) is not None:
            writes.append((name, value.strip()))
    for name, only in (("signing_secret", config.SLACK), ("picker_key", config.GOOGLE),
                       ("app_id", config.GOOGLE)):
        value = getattr(payload, name)
        if value is None:
            continue
        if provider != only:
            raise HTTPException(status_code=422, detail=f"{name}_{only}_only")
        writes.append((name, value.strip()))
    if payload.features is not None:
        unknown = sorted(set(payload.features) - set(config.FEATURES[provider]))
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown_feature:{','.join(unknown)}")
        writes.append(("features", ",".join(f for f in config.FEATURES[provider]
                                            if f in payload.features)))
    for name, value in writes:
        await settings_store.put(db, config.key(provider, name), value, admin.id)
        changed.append(name)
    if changed:
        accounts.audit(db, admin.id, "integration.settings", provider,
                       detail=",".join(changed),
                       metadata={"provider": provider, "fields": changed},
                       ip=client_ip(request),
                       user_agent=request.headers.get("User-Agent", ""))
    await db.commit()
    settings_store.invalidate()
    if before.enabled and payload.enabled is False:
        # off for the whole organisation: everyone's grant is revoked
        await accounts.revoke_provider(db, provider, actor_id=admin.id)
    return await _admin_view(db, provider)


@admin_router.delete("/slack/workspaces/{team_id}")
async def admin_slack_uninstall(team_id: str, request: Request, admin: AdminUser,
                                db: DbSession) -> dict[str, Any]:
    """The app's bot token in one workspace revoked and forgotten; people's own grants stay."""
    row = await db.get(SlackWorkspace, team_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_installed")
    token = accounts.unseal(row.bot_token)
    if token:
        try:
            await accounts._slack_call("auth.revoke", token=token)
        except ProviderError as exc:
            log.info("slack bot revoke failed: %s", exc.code)
    await db.delete(row)
    accounts.audit(db, admin.id, "integration.slack_uninstall", team_id,
                   ip=client_ip(request), user_agent=request.headers.get("User-Agent", ""))
    await db.commit()
    return await _admin_view(db, config.SLACK)
