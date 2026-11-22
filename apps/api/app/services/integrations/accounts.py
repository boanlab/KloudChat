"""Connecting an account: OAuth 2.0 authorization (Google with PKCE and offline access; Slack v2
user tokens on the workspace the administrator installed), sealed tokens, refresh, and revoke on
disconnect or account deletion.

Tokens are sealed with the settings key (`settings_store.encrypt_secret`) and opened only to call
the provider; nothing here logs or returns one."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.integrations import (
    AccountStatus,
    ActionStatus,
    IntegrationAccount,
    IntegrationAction,
    SlackWorkspace,
)
from app.models.user import AuditEvent, utcnow
from app.services import settings_store
from app.services.integrations import config
from app.services.integrations.http import AuthExpired, ProviderError, request

log = logging.getLogger(__name__)

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
SLACK_AUTHORIZE = "https://slack.com/oauth/v2/authorize"
SLACK_API = "https://slack.com/api"

#: an authorization not finished in this long is refused
STATE_TTL = timedelta(minutes=15)
#: refresh this long before the token says it expires
_REFRESH_MARGIN = timedelta(seconds=90)
_locks: dict[str, asyncio.Lock] = {}



def seal(value: str) -> str:
    return settings_store.encrypt_secret(value) if value else ""


def unseal(value: str) -> str:
    return settings_store.decrypt_secret(value) if value else ""


def _digest(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    raw = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def account(db: AsyncSession, user_id: str, provider: str) -> IntegrationAccount | None:
    return (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.user_id == user_id,
        IntegrationAccount.provider == provider))).first()


async def connected(db: AsyncSession, user_id: str) -> dict[str, IntegrationAccount]:
    """The person's working connections by provider (configured providers only); no query
    at all while the administrator has turned none on."""
    usable = [p for p in config.PROVIDERS if (await config.get(p)).usable]
    if not usable:
        return {}
    rows = (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.user_id == user_id,
        col(IntegrationAccount.provider).in_(usable),
        IntegrationAccount.status == AccountStatus.connected.value))).all()
    return {row.provider: row for row in rows}


def granted(row: IntegrationAccount, feature: str) -> bool:
    """Whether the person granted every scope a feature needs (Google lets them untick some)."""
    table = (config.GOOGLE_FEATURES if row.provider == config.GOOGLE
             else config.SLACK_USER_FEATURES)
    needed = set(table.get(feature, ()))
    return needed <= set(row.scopes or [])


def audit(db: AsyncSession, actor_id: str | None, action: str, target: str, *,
          detail: str = "", metadata: dict[str, Any] | None = None, ip: str = "",
          user_agent: str = "") -> None:
    db.add(AuditEvent(actor_id=actor_id, action=action, target=target[:200], detail=detail[:300],
                      event_metadata=metadata, ip=ip, user_agent=user_agent[:400]))


# ── authorization ────────────────────────────────────────────────────────


async def start(db: AsyncSession, user_id: str, provider: str, browser: str) -> str:
    """The provider's consent address; the state, the PKCE verifier and the starting
    browser's nonce (`browser`, also set as a cookie) wait on the person's row."""
    cfg = await config.get(provider)
    if not cfg.usable:
        raise ProviderError("integration_unavailable", 409)
    redirect = await config.redirect_uri(provider)
    if not redirect:
        raise ProviderError("app_base_url_missing", 409)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    row = await account(db, user_id, provider)
    if row is None:
        row = IntegrationAccount(user_id=user_id, provider=provider)
    row.pending_state = _digest(state)
    row.pending_verifier = seal(verifier)
    row.pending_browser = _digest(browser)
    row.pending_at = utcnow()
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()
    if provider == config.GOOGLE:
        query = {
            "client_id": cfg.client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(cfg.user_scopes()),
            "access_type": "offline",
            # a refresh token every time, so a reconnect after revoking works
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        }
        return f"{GOOGLE_AUTHORIZE}?{urlencode(query)}"
    query = {
        "client_id": cfg.client_id,
        # the bot scopes install the app where it is not yet installed (if the workspace lets
        # members install apps); where it is, Slack only authorizes the person
        "scope": ",".join(cfg.install_scopes()),
        "user_scope": ",".join(cfg.user_scopes()),
        "redirect_uri": redirect,
        "state": state,
    }
    return f"{SLACK_AUTHORIZE}?{urlencode(query)}"


def _id_claims(id_token: str) -> dict[str, Any]:
    """Claims of the ID token the token endpoint returned over TLS (no signature check is
    needed for a token received directly from Google, OpenID Connect §3.1.3.7)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        found = json.loads(base64.urlsafe_b64decode(payload))
        return found if isinstance(found, dict) else {}
    except (IndexError, ValueError):
        return {}


async def _google_exchange(cfg: config.ProviderConfig, code: str, verifier: str,
                           redirect: str) -> dict[str, Any]:
    response = await request("POST", GOOGLE_TOKEN, data={
        "grant_type": "authorization_code", "code": code, "client_id": cfg.client_id,
        "client_secret": cfg.client_secret, "redirect_uri": redirect,
        "code_verifier": verifier})
    body = _json(response)
    if response.status_code != 200 or "access_token" not in body:
        raise ProviderError("token_exchange_failed", 502, str(body.get("error") or ""))
    return body


async def _slack_call(method: str, *, token: str | None = None,
                      data: dict[str, Any] | None = None) -> dict[str, Any]:
    response = await request("POST", f"{SLACK_API}/{method}", token=token, data=data or {})
    body = _json(response)
    if not body.get("ok"):
        error = str(body.get("error") or response.status_code)
        if error in ("invalid_auth", "token_revoked", "token_expired", "not_authed",
                     "account_inactive"):
            raise AuthExpired(error)
        raise ProviderError("slack_" + error[:40], 502, error)
    return body


def _json(response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def abandon(db: AsyncSession, provider: str, state: str) -> None:
    """The person declined at the provider: the waiting state goes (a row never connected
    goes with it)."""
    row = (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.provider == provider,
        IntegrationAccount.pending_state == _digest(state)))).first() if state else None
    if row is None:
        return
    if row.status == AccountStatus.pending.value:
        await db.delete(row)
    else:
        row.pending_state, row.pending_verifier, row.pending_at = "", "", None
        row.pending_browser = ""
        db.add(row)
    await db.commit()


async def finish(db: AsyncSession, provider: str, state: str, code: str, browser: str = ""
                 ) -> IntegrationAccount:
    """The person's row once the provider sent them back with a code, in the same browser
    that started (its cookie nonce): a link someone else started connects nothing."""
    row = (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.provider == provider,
        IntegrationAccount.pending_state == _digest(state)))).first() if state else None
    if row is None or row.pending_at is None or utcnow() - _aware(row.pending_at) > STATE_TTL:
        raise ProviderError("state_invalid", 400)
    if not browser or not secrets.compare_digest(row.pending_browser, _digest(browser)):
        raise ProviderError("state_invalid", 400)
    verifier = unseal(row.pending_verifier)
    row.pending_state, row.pending_verifier, row.pending_at = "", "", None
    row.pending_browser = ""
    cfg = await config.get(provider)
    redirect = await config.redirect_uri(provider)
    if not cfg.usable:
        await db.commit()
        raise ProviderError("integration_unavailable", 409)
    try:
        if provider == config.GOOGLE:
            body = await _google_exchange(cfg, code, verifier, redirect)
            claims = _id_claims(str(body.get("id_token") or ""))
            row.access_token = seal(str(body["access_token"]))
            if body.get("refresh_token"):
                row.refresh_token = seal(str(body["refresh_token"]))
            row.expires_at = utcnow() + timedelta(seconds=int(body.get("expires_in") or 3600))
            row.scopes = sorted(str(body.get("scope") or "").split())
            row.external_id = str(claims.get("sub") or "")[:120]
            row.label = str(claims.get("email") or "")[:200]
        else:
            body = await _slack_call("oauth.v2.access", data={
                "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                "code": code, "redirect_uri": redirect})
            team = body.get("team") or {}
            person = body.get("authed_user") or {}
            if not person.get("access_token") or not team.get("id"):
                raise ProviderError("token_exchange_failed", 502)
            row.access_token = seal(str(person["access_token"]))
            row.refresh_token = seal(str(person.get("refresh_token") or ""))
            row.expires_at = (utcnow() + timedelta(seconds=int(person["expires_in"]))
                              if person.get("expires_in") else None)
            row.scopes = sorted(s for s in str(person.get("scope") or "").split(",") if s)
            row.external_id = str(person.get("id") or "")[:120]
            row.team_id = str(team["id"])[:40]
            await _keep_workspace(db, body, installed_by=row.user_id)
            who = await _slack_call("auth.test", token=str(person["access_token"]))
            row.label = f"{team.get('name') or 'Slack'} · @{who.get('user') or ''}"
    except ProviderError:
        await db.commit()
        raise
    row.status = AccountStatus.connected.value
    row.error = ""
    row.updated_at = utcnow()
    db.add(row)
    audit(db, row.user_id, "integration.connect", provider,
          metadata={"provider": provider, "scopes": row.scopes})
    await db.commit()
    await db.refresh(row)
    return row


async def _keep_workspace(db: AsyncSession, body: dict[str, Any], *,
                          installed_by: str) -> None:
    """The workspace's bot token when this authorization installed (or re-installed) the app;
    an authorization of a person alone leaves the stored install as it is."""
    team = body.get("team") or {}
    bot = str(body.get("access_token") or "")
    if not team.get("id") or body.get("token_type", "bot") != "bot" or not bot:
        return
    row = await db.get(SlackWorkspace, str(team["id"])[:40])
    if row is None:
        row = SlackWorkspace(team_id=str(team["id"])[:40], installed_by=installed_by)
        audit(db, installed_by, "integration.slack_install", row.team_id,
              metadata={"team": str(team.get("name") or "")[:80]})
    row.name = str(team.get("name") or "")[:200]
    row.bot_token = seal(bot)
    row.bot_user_id = str(body.get("bot_user_id") or "")[:40]
    row.scopes = sorted(s for s in str(body.get("scope") or "").split(",") if s)
    row.updated_at = utcnow()
    db.add(row)


async def bot_token(db: AsyncSession, team_id: str) -> str:
    """The installed app's bot token in a workspace, or ""."""
    row = await db.get(SlackWorkspace, team_id) if team_id else None
    return unseal(row.bot_token) if row is not None else ""


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ── using a token ────────────────────────────────────────────────────────


async def _needs_auth(db: AsyncSession, row: IntegrationAccount, detail: str) -> None:
    row.status = AccountStatus.needs_auth.value
    row.error = detail[:300]
    row.updated_at = utcnow()
    db.add(row)
    await db.commit()


async def access_token(db: AsyncSession, row: IntegrationAccount) -> str:
    """A working access token, refreshed when it is about to lapse; `AuthExpired` when the
    grant is gone (the row is marked for reconnecting)."""
    if row.status != AccountStatus.connected.value:
        raise AuthExpired(row.status)
    lock = _locks.setdefault(row.id, asyncio.Lock())
    async with lock:
        await db.refresh(row)
        if row.expires_at is None or _aware(row.expires_at) - _REFRESH_MARGIN > utcnow():
            token = unseal(row.access_token)
            if not token:
                await _needs_auth(db, row, "token_unreadable")
                raise AuthExpired("token_unreadable")
            return token
        refresh = unseal(row.refresh_token)
        if not refresh:
            await _needs_auth(db, row, "no_refresh_token")
            raise AuthExpired("no_refresh_token")
        cfg = await config.get(row.provider)
        if row.provider == config.GOOGLE:
            response = await request("POST", GOOGLE_TOKEN, data={
                "grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": cfg.client_id, "client_secret": cfg.client_secret})
            body = _json(response)
            if response.status_code in (400, 401) and body.get("error") in (
                    "invalid_grant", "unauthorized_client", "invalid_client"):
                await _needs_auth(db, row, str(body.get("error")))
                raise AuthExpired(str(body.get("error")))
            if response.status_code != 200 or "access_token" not in body:
                raise ProviderError("token_refresh_failed", 502, str(body.get("error") or ""))
            token = str(body["access_token"])
            expires = int(body.get("expires_in") or 3600)
            new_refresh = str(body.get("refresh_token") or "")
        else:
            try:
                body = await _slack_call("oauth.v2.access", data={
                    "grant_type": "refresh_token", "refresh_token": refresh,
                    "client_id": cfg.client_id, "client_secret": cfg.client_secret})
            except AuthExpired as exc:
                await _needs_auth(db, row, exc.detail)
                raise
            except ProviderError as exc:
                if exc.detail in ("invalid_refresh_token", "invalid_grant"):
                    await _needs_auth(db, row, exc.detail)
                    raise AuthExpired(exc.detail) from None
                raise
            person = body.get("authed_user") or body
            token = str(person.get("access_token") or "")
            expires = int(person.get("expires_in") or 43200)
            new_refresh = str(person.get("refresh_token") or "")
        row.access_token = seal(token)
        if new_refresh:
            row.refresh_token = seal(new_refresh)
        row.expires_at = utcnow() + timedelta(seconds=expires)
        row.updated_at = utcnow()
        db.add(row)
        await db.commit()
        return token


async def expire(db: AsyncSession, row: IntegrationAccount) -> None:
    """The provider refused the access token early: the next use refreshes it."""
    row.expires_at = utcnow()
    db.add(row)
    await db.commit()


async def touch(db: AsyncSession, row: IntegrationAccount) -> None:
    row.last_used_at = utcnow()
    db.add(row)
    await db.commit()


# ── disconnecting ────────────────────────────────────────────────────────


async def _revoke_at_provider(row: IntegrationAccount) -> bool:
    """Best effort: the grant is dropped here whether or not the provider answers."""
    token = unseal(row.refresh_token) or unseal(row.access_token)
    if not token:
        return False
    try:
        if row.provider == config.GOOGLE:
            response = await request("POST", GOOGLE_REVOKE, data={"token": token}, retries=1)
            return response.status_code in (200, 400)
        await _slack_call("auth.revoke", token=unseal(row.access_token) or token)
        return True
    except ProviderError as exc:
        log.info("revoke at %s failed: %s", row.provider, exc.code)
        return False


async def disconnect(db: AsyncSession, row: IntegrationAccount, *, actor_id: str | None,
                     reason: str = "user", ip: str = "", user_agent: str = "") -> bool:
    """Grant revoked at the provider, row and waiting writes gone; True if the provider
    confirmed the revoke."""
    revoked = await _revoke_at_provider(row)
    await db.exec(delete(IntegrationAction).where(
        IntegrationAction.user_id == row.user_id,
        IntegrationAction.provider == row.provider,
        IntegrationAction.status == ActionStatus.pending.value))
    audit(db, actor_id, "integration.disconnect", row.provider,
          metadata={"provider": row.provider, "reason": reason, "revoked": revoked,
                    "user": row.user_id}, ip=ip, user_agent=user_agent)
    await db.delete(row)
    await db.commit()
    _locks.pop(row.id, None)
    return revoked


async def revoke_provider(db: AsyncSession, provider: str, *, actor_id: str | None) -> int:
    """The administrator turned a provider off for everyone: every person's grant revoked."""
    rows = (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.provider == provider))).all()
    for row in rows:
        await disconnect(db, row, actor_id=actor_id, reason="disabled")
    return len(rows)


async def revoke_all(db: AsyncSession, user_id: str, *, actor_id: str | None) -> int:
    """Account deletion: every grant revoked and removed (before the user row goes)."""
    rows = (await db.exec(select(IntegrationAccount).where(
        IntegrationAccount.user_id == user_id))).all()
    for row in rows:
        await disconnect(db, row, actor_id=actor_id, reason="account_deleted")
    await db.exec(delete(IntegrationAction).where(col(IntegrationAction.user_id) == user_id))
    return len(rows)
