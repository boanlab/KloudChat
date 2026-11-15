"""Shared setup for the Google and Slack tests: an in-memory database with the tables they
touch, the administrator's settings, and a mocked provider (`httpx.MockTransport`) — no
test ever reaches Google or Slack."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import integrations as integration_models
from app.models.settings import SystemSetting  # noqa: F401 — the table
from app.models.user import AuditEvent, User, UserRole
from app.services import settings_store
from app.services.integrations import actions, common, config, http

TABLES = ["users", "audit_events", "system_settings", "integration_accounts", "slack_workspaces",
          "integration_imports", "files", "projects", "design_systems", "artifacts", "sessions",
          "integration_actions"]


def settings_for(google: bool = True, slack: bool = False, **extra: str) -> dict[str, str]:
    values = {settings_store.APP_BASE_URL: "https://chat.example.ac.kr"}
    if google:
        values.update({
            config.key("google", "enabled"): "1",
            config.key("google", "client_id"): "client-1.apps.googleusercontent.com",
            config.key("google", "client_secret"): "google-secret",
        })
    if slack:
        values.update({
            config.key("slack", "enabled"): "1",
            config.key("slack", "client_id"): "111.222",
            config.key("slack", "client_secret"): "slack-secret",
            config.key("slack", "signing_secret"): "signing-secret",
        })
    values.update(extra)
    return values


class Provider:
    """A mocked Google/Slack: `routes` maps `METHOD host/path` to a handler or a reply."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.routes: dict[str, Any] = {}

    def on(self, key: str, reply: Any) -> None:
        self.routes[key] = reply

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = f"{request.method} {request.url.host}{request.url.path}"
        reply = self.routes.get(key)
        if reply is None:
            return httpx.Response(404, json={"error": {"message": f"unmocked {key}"}})
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, httpx.Response):
            return reply
        status, body = reply if isinstance(reply, tuple) else (200, reply)
        return httpx.Response(status, json=body)

    def sent(self, key: str) -> list[httpx.Request]:
        return [c for c in self.calls if f"{c.method} {c.url.host}{c.url.path}" == key]


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content or b"{}")


@pytest.fixture
async def world(monkeypatch):
    metadata = MetaData()
    wanted = set(TABLES) | EXTRA_TABLES
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in wanted:
            continue
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    values = settings_for()

    real_load = settings_store._load

    async def all_values(force: bool = False) -> dict[str, str]:
        # the test's settings, under whatever the code under test stored
        async with maker() as session:
            return {**values, **(await real_load(session))}

    monkeypatch.setattr(settings_store, "all_values", all_values)
    monkeypatch.setattr(common, "SessionLocal", maker)
    monkeypatch.setattr(actions, "SessionLocal", maker)
    from app.services.integrations import slack_bot, slack_notify

    monkeypatch.setattr(slack_notify, "SessionLocal", maker)
    monkeypatch.setattr(slack_bot, "SessionLocal", maker)
    provider = Provider()
    http.use_transport(httpx.MockTransport(provider.handle))

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(http, "_sleep", no_wait)
    async with maker() as db:
        member = User(email="member@example.ac.kr", name="구성원", password_hash="h")
        admin = User(email="admin@example.ac.kr", name="관리자", password_hash="h",
                     role=UserRole.admin)
        db.add_all([member, admin])
        await db.commit()
        yield SimpleNamespace(db=db, maker=maker, values=values, provider=provider,
                              member=member, admin=admin)
    http.use_transport(None)
    await engine.dispose()


#: tables a later test module adds (Drive imports, Slack channels)
EXTRA_TABLES: set[str] = set()


def request_stub() -> SimpleNamespace:
    return SimpleNamespace(headers={"User-Agent": "pytest"},
                           client=SimpleNamespace(host="127.0.0.1"))


async def connected_google(db: AsyncSession, user: User, *, scopes: list[str] | None = None,
                           expires_in: int = 3600) -> integration_models.IntegrationAccount:
    from datetime import timedelta

    from app.models.user import utcnow
    from app.services.integrations import accounts

    row = integration_models.IntegrationAccount(
        user_id=user.id, provider="google", status="connected", label=user.email,
        external_id="sub-1",
        scopes=scopes or sorted({*config.GOOGLE_IDENTITY, config.DRIVE_FILE}),
        access_token=accounts.seal("ya29.access"), refresh_token=accounts.seal("1//refresh"),
        expires_at=utcnow() + timedelta(seconds=expires_in))
    db.add(row)
    await db.commit()
    return row


def all_audits(db_rows: list[Any]) -> list[str]:
    return [r.action for r in db_rows if isinstance(r, AuditEvent)]


Handler = Callable[[httpx.Request], Any]
