"""Admin CSV downloads (users, audit trail) and the audit trail's column sort."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request

from app.models.user import AuditEvent, User, UserRole, UserStatus
from app.routers import admin as admin_router
from app.routers import usage as usage_router
from app.services import csv_export

_DDL = (
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
        monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
        litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT, allowed_models TEXT, preferences TEXT, created_at DATETIME,
        last_active_at DATETIME, email_verified_at DATETIME
    )
    """,
    """
    CREATE TABLE audit_events (
        id TEXT PRIMARY KEY, at DATETIME, actor_id TEXT, action TEXT, target TEXT, detail TEXT,
        metadata TEXT, ip TEXT, user_agent TEXT, severity TEXT
    )
    """,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("10.0.0.9", 1234),
        }
    )


def _admin() -> User:
    return User(id="adm", email="admin@example.com", name="관리자", password_hash="x",
                role=UserRole.admin, status=UserStatus.active)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    session = AsyncSession(engine, expire_on_commit=False)
    session.add(_admin())
    session.add(User(id="u1", email="hong@example.com", name="홍길동", password_hash="x",
                     status=UserStatus.active, monthly_credits=1000, credits_used=250,
                     created_at=T0, last_active_at=T0 + timedelta(hours=3)))
    # A name a spreadsheet would read as a formula.
    session.add(User(id="u2", email="kim@example.com", name="=HYPERLINK(\"x\")", password_hash="x",
                     status=UserStatus.pending, monthly_credits=0, credits_used=0,
                     created_at=T0 + timedelta(days=1)))
    session.add(User(id="u3", email="lee@example.com", name="이몽룡, 대리", password_hash="x",
                     status=UserStatus.suspended, monthly_credits=500, credits_used=600,
                     created_at=T0 - timedelta(days=1)))
    for i, (action, target, ip) in enumerate(
        [("login", "hong@example.com", "10.0.0.3"), ("user.approve", "kim@example.com", "10.0.0.1"),
         ("credits.set", "lee@example.com", ""), ("login", "admin@example.com", "10.0.0.2")]
    ):
        session.add(AuditEvent(id=f"e{i}", actor_id="u1" if i % 2 == 0 else "adm", action=action,
                               target=target, ip=ip, at=T0 + timedelta(hours=i)))
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


async def _body(response) -> list[list[str]]:
    chunks = [chunk async for chunk in response.body_iterator]
    text = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


async def _audits(db, action: str) -> list[AuditEvent]:
    return list((await db.exec(select(AuditEvent).where(AuditEvent.action == action))).all())


def test_cells_are_spreadsheet_safe() -> None:
    assert csv_export.cell("=1+1") == "'=1+1"
    assert csv_export.cell("@cmd") == "'@cmd"
    assert csv_export.cell(-5) == -5
    assert csv_export.cell(None) == ""
    assert csv_export.cell(UserStatus.active) == "active"
    # Times in the service zone (Asia/Seoul by default).
    assert csv_export.cell(datetime(2026, 10, 1, 0, 0, tzinfo=UTC)).startswith("2026-10-01 09:00")


@pytest.mark.asyncio
async def test_users_export_follows_the_screen_filters_and_is_recorded(db) -> None:
    response = await admin_router.export_users(
        _admin(), db, _request(), tab="all", q="", sort="credits", order="desc"
    )
    assert response.media_type.startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = await _body(response)
    assert rows[0][:4] == ["name", "email", "role", "status"]
    # Most used first; a tie by name ("=…" sorts before Hangul).
    assert [r[1] for r in rows[1:]] == [
        "lee@example.com", "hong@example.com", "kim@example.com", "admin@example.com",
    ]
    by_email = {r[1]: r for r in rows[1:]}
    assert by_email["hong@example.com"][4:7] == ["1000", "250", "750"]
    assert by_email["lee@example.com"][0] == "이몽룡, 대리"
    assert by_email["kim@example.com"][0].startswith("'=HYPERLINK")

    pending = await _body(
        await admin_router.export_users(_admin(), db, _request(), tab="pending", q="", sort="name",
                                        order="asc")
    )
    assert [r[1] for r in pending[1:]] == ["kim@example.com"]

    events = await _audits(db, "user.export")
    assert len(events) == 2
    assert events[0].actor_id == "adm" and events[0].ip == "10.0.0.9"
    assert events[0].event_metadata["rows"] in (1, 4)


async def _list(db, **kw):
    params = {"page": 1, "size": 50, "severity": None, "action": None, "account": None, "q": None,
              "q_actions": None, "since": None, "until": None}
    params.update(kw)
    return await usage_router.audit_log(_admin(), db, **params)


@pytest.mark.asyncio
async def test_the_audit_trail_sorts_by_a_column_on_the_server(db) -> None:
    newest = await _list(db)
    assert [r["id"] for r in newest["items"]] == ["e3", "e2", "e1", "e0"]
    oldest = await _list(db, sort="at", order="asc")
    assert [r["id"] for r in oldest["items"]] == ["e0", "e1", "e2", "e3"]
    by_action = await _list(db, sort="action", order="asc")
    assert [r["action"] for r in by_action["items"]] == [
        "credits.set", "login", "login", "user.approve",
    ]
    by_ip = await _list(db, sort="ip", order="desc")
    # No address last whichever way.
    assert [r["ip"] for r in by_ip["items"]] == ["10.0.0.3", "10.0.0.2", "10.0.0.1", ""]
    by_ip_up = await _list(db, sort="ip", order="asc")
    assert by_ip_up["items"][-1]["ip"] == ""


@pytest.mark.asyncio
async def test_the_audit_export_keeps_filters_and_order_and_is_itself_recorded(db) -> None:
    response = await usage_router.export_audit_log(
        _admin(), db, _request(), severity=None, action="login", account=None, q=None,
        q_actions=None, since=None, until=None, sort="at", order="asc",
    )
    rows = await _body(response)
    assert rows[0][:3] == ["at", "actor", "action"]
    assert [r[3] for r in rows[1:]] == ["hong@example.com", "admin@example.com"]
    assert rows[1][1] == "hong@example.com" and rows[2][1] == "admin@example.com"
    (event,) = await _audits(db, "audit.export")
    assert event.event_metadata == {"rows": 2, "filters": {"action": "login", "sort": "at",
                                                          "order": "asc"}}
