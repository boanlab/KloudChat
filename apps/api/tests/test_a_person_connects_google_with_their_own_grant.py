"""Connecting Google and Slack per person, each with their own account (a personal gmail.com
account too): PKCE authorization with the installation's one client, tokens sealed at rest,
refresh before expiry, reconnect when the grant is gone, revoke on disconnect, on account
deletion and when the administrator turns a provider off, and secrets never echoed."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from integration_fixtures import (  # noqa: F401
    body,
    connected_google,
    form,
    request_stub,
    settings_for,
    world,
)
from sqlmodel import select

from app.models.integrations import IntegrationAccount, IntegrationAction
from app.models.user import AuditEvent, utcnow
from app.routers import integrations as router
from app.services.integrations import accounts, config
from app.services.integrations.http import AuthExpired, ProviderError

TOKEN = "POST oauth2.googleapis.com/token"
REVOKE = "POST oauth2.googleapis.com/revoke"


def _id_token(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{payload}.sig"


#: the browser's cookie jar: the connect cookie goes back with the callback
_jar: dict[str, str] = {}


async def _connect(world, provider: str, user):  # noqa: F811
    from fastapi import Response

    response = Response()
    out = await router.connect(provider, response, user, world.db)
    cookie = response.headers.get("set-cookie", "")
    name, _, rest = cookie.partition("=")
    _jar[name] = rest.split(";")[0]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    return out


async def _callback(world, provider: str, *, other_browser: bool = False,  # noqa: F811
                    **query):
    from types import SimpleNamespace

    cookies = {} if other_browser else dict(_jar)
    return await router.callback(provider, SimpleNamespace(cookies=cookies), world.db, **query)


async def _audits(world, action: str) -> list[AuditEvent]:  # noqa: F811
    return list((await world.db.exec(select(AuditEvent).where(
        AuditEvent.action == action))).all())


@pytest.mark.asyncio
async def test_connecting_google_asks_for_the_allowed_scopes_with_pkce_and_seals_the_tokens(
        world):  # noqa: F811
    world.values[config.key("google", "features")] = "drive"
    started = await _connect(world, "google", world.member)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started["url"]).query).items()}
    assert started["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["redirect_uri"] == "https://chat.example.ac.kr/api/integrations/google/callback"
    assert query["access_type"] == "offline" and query["code_challenge_method"] == "S256"
    # drive.file only: no restricted scope, no calendar
    assert query["scope"].split() == ["openid", "email", config.DRIVE_FILE]
    assert "hd" not in query  # any Google account, not one Workspace domain

    def token(request: httpx.Request):
        sent = form(request)
        # the verifier matches the challenge sent to the browser
        digest = hashlib.sha256(sent["code_verifier"].encode()).digest()
        assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"]
        assert sent["client_secret"] == "google-secret" and sent["code"] == "auth-code"
        return {"access_token": "ya29.fresh", "refresh_token": "1//kept", "expires_in": 3599,
                "scope": f"openid email {config.DRIVE_FILE}",
                "id_token": _id_token({"sub": "g-42", "email": "member@gmail.com"})}

    world.provider.on(TOKEN, token)
    back = await _callback(world, "google", code="auth-code", state=query["state"])
    assert back.headers["location"] == "/settings/connections?connected=google"

    row = (await world.db.exec(select(IntegrationAccount))).one()
    assert row.status == "connected" and row.label == "member@gmail.com"
    assert accounts.granted(row, "drive")
    # sealed at rest; nothing readable in the row
    assert "ya29.fresh" not in row.access_token and "1//kept" not in row.refresh_token
    assert accounts.unseal(row.access_token) == "ya29.fresh"
    assert row.pending_state == "" and row.pending_verifier == ""
    assert [a.target for a in await _audits(world, "integration.connect")] == ["google"]

    listed = await router.list_integrations(world.member, world.db)
    assert listed[0]["connection"]["features"] == ["drive"]
    assert "access_token" not in json.dumps(listed, default=str)

    # the same state cannot be used twice
    again = await _callback(world, "google", code="auth-code", state=query["state"])
    assert again.headers["location"].endswith("error=state_invalid&provider=google")


@pytest.mark.asyncio
async def test_a_stale_or_declined_authorization_connects_nothing(world):  # noqa: F811
    started = await _connect(world, "google", world.member)
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    row = (await world.db.exec(select(IntegrationAccount))).one()
    row.pending_at = utcnow() - timedelta(hours=1)
    world.db.add(row)
    await world.db.commit()
    back = await _callback(world, "google", code="c", state=state)
    assert "error=state_invalid" in back.headers["location"]

    started = await _connect(world, "google", world.member)
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    back = await _callback(world, "google", error="access_denied", state=state)
    assert "error=denied" in back.headers["location"]
    # never connected: no row is left behind
    assert (await world.db.exec(select(IntegrationAccount))).all() == []
    assert world.provider.calls == []


@pytest.mark.asyncio
async def test_a_link_someone_else_started_connects_nothing_in_my_browser(world):  # noqa: F811
    started = await _connect(world, "google", world.admin)
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    world.provider.on(TOKEN, {"access_token": "ya29.x", "expires_in": 3600, "scope": "email"})
    back = await _callback(world, "google", other_browser=True, code="c", state=state)
    assert "error=state_invalid" in back.headers["location"]
    assert world.provider.sent(TOKEN) == []
    assert (await world.db.exec(select(IntegrationAccount))).one().status == "pending"


@pytest.mark.asyncio
async def test_nothing_connects_while_the_administrator_has_not_set_google_up(world):  # noqa: F811
    world.values.update({config.key("google", "enabled"): "0"})
    with pytest.raises(Exception) as refused:
        await _connect(world, "google", world.member)
    assert getattr(refused.value, "detail", "") == "integration_unavailable"
    assert await router.list_integrations(world.member, world.db) == []
    assert await accounts.connected(world.db, world.member.id) == {}


@pytest.mark.asyncio
async def test_an_expiring_token_is_refreshed_and_a_revoked_grant_asks_to_reconnect(
        world):  # noqa: F811
    row = await connected_google(world.db, world.member, expires_in=10)
    world.provider.on(TOKEN, lambda r: {"access_token": "ya29.next", "expires_in": 3600}
                      if form(r)["refresh_token"] == "1//refresh" else (400, {}))
    assert await accounts.access_token(world.db, row) == "ya29.next"
    assert form(world.provider.sent(TOKEN)[0])["grant_type"] == "refresh_token"
    # fresh now: no second refresh
    assert await accounts.access_token(world.db, row) == "ya29.next"
    assert len(world.provider.sent(TOKEN)) == 1

    await accounts.expire(world.db, row)
    world.provider.on(TOKEN, (400, {"error": "invalid_grant"}))
    with pytest.raises(AuthExpired):
        await accounts.access_token(world.db, row)
    await world.db.refresh(row)
    assert row.status == "needs_auth"
    listed = await router.list_integrations(world.member, world.db)
    assert listed[0]["connection"]["status"] == "needs_auth"


@pytest.mark.asyncio
async def test_a_rate_limited_refresh_backs_off_and_tries_again(world):  # noqa: F811
    row = await connected_google(world.db, world.member, expires_in=0)
    replies = iter([httpx.Response(429, headers={"Retry-After": "2"}),
                    httpx.Response(503),
                    httpx.Response(200, json={"access_token": "ya29.ok", "expires_in": 60})])
    world.provider.on(TOKEN, lambda _r: next(replies))
    assert await accounts.access_token(world.db, row) == "ya29.ok"
    assert len(world.provider.sent(TOKEN)) == 3


@pytest.mark.asyncio
async def test_disconnecting_revokes_the_grant_and_forgets_waiting_writes(world):  # noqa: F811
    await connected_google(world.db, world.member)
    world.db.add(IntegrationAction(user_id=world.member.id, provider="google",
                                   kind="drive.export", payload={"title": "x"}))
    await world.db.commit()
    world.provider.on(REVOKE, {})
    await router.disconnect("google", request_stub(), world.member, world.db)
    assert form(world.provider.sent(REVOKE)[0]) == {"token": "1//refresh"}
    assert (await world.db.exec(select(IntegrationAccount))).all() == []
    assert (await world.db.exec(select(IntegrationAction))).all() == []
    audit = (await _audits(world, "integration.disconnect"))[0]
    assert audit.event_metadata["revoked"] is True and audit.actor_id == world.member.id


@pytest.mark.asyncio
async def test_an_unreachable_provider_still_lets_a_person_disconnect(world):  # noqa: F811
    await connected_google(world.db, world.member)
    world.provider.on(REVOKE, httpx.Response(503))
    await router.disconnect("google", request_stub(), world.member, world.db)
    assert (await world.db.exec(select(IntegrationAccount))).all() == []
    assert (await _audits(world, "integration.disconnect"))[0].event_metadata["revoked"] is False


@pytest.mark.asyncio
async def test_deleting_an_account_revokes_every_grant_first(world):  # noqa: F811
    await connected_google(world.db, world.member)
    world.provider.on(REVOKE, {})
    assert await accounts.revoke_all(world.db, world.member.id, actor_id=world.admin.id) == 1
    assert world.provider.sent(REVOKE)
    audit = (await _audits(world, "integration.disconnect"))[0]
    assert audit.event_metadata["reason"] == "account_deleted"
    assert audit.actor_id == world.admin.id


@pytest.mark.asyncio
async def test_the_administrator_sets_up_a_provider_without_the_secret_ever_coming_back(
        world):  # noqa: F811
    world.values.clear()
    world.values.update(settings_for(google=False))
    view = await router.admin_save(
        "google", router.ProviderSetup(enabled=True, client_id="cid", client_secret="s3cr3t-xyz",
                                       features=["drive"]),
        request_stub(), world.admin, world.db)
    assert view["clientSecretSet"] and view["clientSecretPreview"] == "…-xyz"
    assert "s3cr3t-xyz" not in json.dumps(view)
    assert view["features"] == ["drive"] and view["usable"]
    # stored sealed
    from app.models.settings import SystemSetting

    stored = await world.db.get(SystemSetting, config.key("google", "client_secret"))
    assert stored.secret and "s3cr3t" not in stored.value
    # null keeps the secret
    view = await router.admin_save("google", router.ProviderSetup(client_id="cid2"),
                                   request_stub(), world.admin, world.db)
    assert view["clientSecretSet"] and view["clientId"] == "cid2"
    with pytest.raises(Exception) as refused:
        await router.admin_save("google", router.ProviderSetup(features=["gmail"]),
                                request_stub(), world.admin, world.db)
    assert getattr(refused.value, "detail", "") == "unknown_feature:gmail"
    audit = await _audits(world, "integration.settings")
    assert audit and "s3cr3t" not in json.dumps([a.event_metadata for a in audit])


@pytest.mark.asyncio
async def test_a_person_connects_their_own_slack_and_installs_the_app_where_it_is_missing(
        world):  # noqa: F811
    world.values.update(settings_for(google=False, slack=True))
    started = await _connect(world, "slack", world.member)
    query = {k: v[0] for k, v in parse_qs(urlsplit(started["url"]).query).items()}
    assert started["url"].startswith("https://slack.com/oauth/v2/authorize?")
    assert "chat:write" in query["user_scope"].split(",")
    # bot scopes too: a workspace without the app gets it installed (if it lets members)
    assert "app_mentions:read" in query["scope"].split(",")
    world.provider.on("POST slack.com/api/oauth.v2.access", {
        "ok": True, "access_token": "xoxb-team", "token_type": "bot", "bot_user_id": "UB",
        "scope": "chat:write,app_mentions:read", "team": {"id": "T1", "name": "단국"},
        "authed_user": {"id": "U1", "access_token": "xoxp-mine",
                        "scope": "channels:read,groups:read,chat:write"}})
    world.provider.on("POST slack.com/api/auth.test", {"ok": True, "user": "jn"})
    back = await _callback(world, "slack", code="c", state=query["state"])
    assert back.headers["location"] == "/settings/connections?connected=slack"
    row = (await world.db.exec(select(IntegrationAccount))).one()
    assert row.label == "단국 · @jn" and row.external_id == "U1" and row.team_id == "T1"
    assert accounts.granted(row, "post") and not accounts.granted(row, "read")
    # the workspace's bot token, sealed
    from app.models.integrations import SlackWorkspace

    team = await world.db.get(SlackWorkspace, "T1")
    assert team.installed_by == world.member.id and "xoxb" not in team.bot_token
    assert await accounts.bot_token(world.db, "T1") == "xoxb-team"
    assert [a.target for a in await _audits(world, "integration.slack_install")] == ["T1"]
    view = await router.admin_integrations(world.admin, world.db)
    slack = next(p for p in view["providers"] if p["provider"] == "slack")
    assert slack["workspaces"][0]["name"] == "단국" and slack["connectedPeople"] == 1
    assert "xox" not in json.dumps(view, default=str)


@pytest.mark.asyncio
async def test_a_workspace_that_does_not_let_members_install_sends_the_person_back(
        world):  # noqa: F811
    world.values.update(settings_for(google=False, slack=True))
    started = await _connect(world, "slack", world.member)
    state = parse_qs(urlsplit(started["url"]).query)["state"][0]
    back = await _callback(world, "slack", error="access_denied", state=state)
    assert back.headers["location"] == "/settings/connections?error=denied&provider=slack"
    assert (await world.db.exec(select(IntegrationAccount))).all() == []


@pytest.mark.asyncio
async def test_the_administrator_sees_who_connected_but_no_token_and_turning_off_disconnects_all(
        world):  # noqa: F811
    await connected_google(world.db, world.member)
    await connected_google(world.db, world.admin)
    listed = await router.admin_accounts("google", world.admin, world.db)
    assert {r["email"] for r in listed} == {"member@example.ac.kr", "admin@example.ac.kr"}
    assert "ya29" not in json.dumps(listed, default=str)
    world.provider.on(REVOKE, {})
    await router.admin_save("google", router.ProviderSetup(enabled=False), request_stub(),
                            world.admin, world.db)
    assert len(world.provider.sent(REVOKE)) == 2
    assert (await world.db.exec(select(IntegrationAccount))).all() == []
    reasons = {a.event_metadata["reason"] for a in await _audits(world, "integration.disconnect")}
    assert reasons == {"disabled"}


@pytest.mark.asyncio
async def test_a_provider_error_never_carries_a_token(world):  # noqa: F811
    row = await connected_google(world.db, world.member, expires_in=0)
    world.provider.on(TOKEN, (500, {"error": "backend", "token": "ya29.leak"}))
    with pytest.raises(ProviderError) as failed:
        await accounts.access_token(world.db, row)
    assert "ya29" not in str(failed.value) and "ya29" not in failed.value.detail
