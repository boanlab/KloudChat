"""Settings › 보안 pages its two lists on the server: sign-ins and the access log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.security import hash_refresh_token
from app.models.user import AuditEvent, RefreshToken, User
from app.routers import auth

NOW = datetime.now(UTC)


class _Request:
    def __init__(self, raw: str | None):
        self.cookies = {settings.refresh_cookie_name: raw} if raw else {}


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: SQLModel.metadata.create_all(sync, tables=[RefreshToken.__table__])
        )
        # audit_events carries a JSONB column SQLite cannot render; same DDL as test_audit_log.
        await conn.exec_driver_sql(
            "CREATE TABLE audit_events (id TEXT PRIMARY KEY, at DATETIME, actor_id TEXT,"
            " action TEXT, target TEXT, detail TEXT, metadata TEXT, ip TEXT, user_agent TEXT,"
            " severity TEXT)"
        )
    session = AsyncSession(engine, expire_on_commit=False)
    # 23 live sign-ins for hong, minutes apart; "cur" is the oldest-active one, so it
    # would land on the last page if it were not pinned.
    for i in range(22):
        session.add(
            RefreshToken(
                user_id="u1",
                token_hash=f"h{i}",
                family_id=f"f{i:02d}",
                created_at=NOW - timedelta(minutes=i + 1),
                expires_at=NOW + timedelta(days=7),
                ip="10.0.0.1",
                user_agent="curl/8.5",
            )
        )
    session.add(
        RefreshToken(
            user_id="u1",
            token_hash=hash_refresh_token("mine"),
            family_id="cur",
            created_at=NOW - timedelta(days=2),
            expires_at=NOW + timedelta(days=7),
        )
    )
    # Ended and expired sign-ins, and someone else's, never count.
    session.add(
        RefreshToken(
            user_id="u1",
            token_hash="gone",
            family_id="gone",
            revoked_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    session.add(
        RefreshToken(
            user_id="u1", token_hash="old", family_id="old", expires_at=NOW - timedelta(days=1)
        )
    )
    for i in range(5):
        session.add(
            RefreshToken(
                user_id="u2",
                token_hash=f"k{i}",
                family_id=f"k{i}",
                expires_at=NOW + timedelta(days=7),
            )
        )
    # 45 account events for hong, one admin-only event, and 7 for kim.
    for i in range(45):
        session.add(
            AuditEvent(actor_id="u1", action="login", at=NOW - timedelta(hours=i), ip="10.0.0.1")
        )
    session.add(AuditEvent(actor_id="u1", action="governance.update", at=NOW))
    for i in range(7):
        session.add(AuditEvent(actor_id="u2", action="login", at=NOW - timedelta(minutes=i)))
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


_USERS = {"u1": "hong@example.com", "u2": "kim@example.com"}


async def _user(db, uid="u1"):
    # Only the id matters; the users table carries Postgres-only columns.
    return User(id=uid, email=_USERS[uid], name=uid, password_hash="x")


@pytest.mark.asyncio
async def test_the_current_sign_in_leads_page_one_then_newest_first(db):
    first = await auth.my_sessions(await _user(db), _Request("mine"), db, page=1, size=10)
    assert first.total == 23 and first.others == 22 and first.page == 1 and first.size == 10
    assert len(first.items) == 10
    assert first.items[0].family_id == "cur" and first.items[0].current
    rest = [s.family_id for s in first.items[1:]]
    assert rest == [f"f{i:02d}" for i in range(9)]
    assert not any(s.current for s in first.items[1:])


@pytest.mark.asyncio
async def test_sign_in_pages_do_not_overlap_and_the_last_is_short(db):
    user = await _user(db)
    pages = [await auth.my_sessions(user, _Request("mine"), db, page=p, size=10) for p in (1, 2, 3)]
    assert [len(p.items) for p in pages] == [10, 10, 3]
    ids = [s.family_id for p in pages for s in p.items]
    assert len(ids) == len(set(ids)) == 23
    assert "cur" not in ids[10:]
    beyond = await auth.my_sessions(user, _Request("mine"), db, page=4, size=10)
    assert beyond.items == [] and beyond.total == 23


@pytest.mark.asyncio
async def test_sign_ins_are_scoped_to_the_caller(db):
    kim = await auth.my_sessions(await _user(db, "u2"), _Request("mine"), db, page=1, size=10)
    assert kim.total == 5 and kim.others == 5
    assert {s.family_id for s in kim.items} == {f"k{i}" for i in range(5)}
    # hong's cookie does not mark anything current on kim's list.
    assert not any(s.current for s in kim.items)


@pytest.mark.asyncio
async def test_without_a_cookie_nothing_is_pinned(db):
    page = await auth.my_sessions(await _user(db), _Request(None), db, page=3, size=10)
    assert page.total == 23 and [s.family_id for s in page.items][-1] == "cur"


@pytest.mark.asyncio
async def test_the_access_log_pages_newest_first_with_a_total(db):
    user = await _user(db)
    first = await auth.my_access_log(user, db, page=1, size=20)
    assert first.total == 45 and len(first.items) == 20
    ats = [e.at for e in first.items]
    assert ats == sorted(ats, reverse=True)
    third = await auth.my_access_log(user, db, page=3, size=20)
    assert len(third.items) == 5
    assert not {e.id for e in first.items} & {e.id for e in third.items}
    assert (await auth.my_access_log(user, db, page=9, size=20)).items == []
    # Admin-only actions and other accounts stay out.
    assert all(e.action == "login" for e in first.items)
    kim = await auth.my_access_log(await _user(db, "u2"), db, page=1, size=20)
    assert kim.total == 7


@pytest.mark.asyncio
async def test_the_wire_shape_and_page_bounds(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.db import get_session
    from app.core.deps import current_user

    app = FastAPI()
    app.include_router(auth.router)
    user = await _user(db)

    async def override_user():
        return user

    async def override_db():
        yield db

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[get_session] = override_db
    with TestClient(app) as client:
        client.cookies.set(settings.refresh_cookie_name, "mine")
        sessions = client.get("/auth/me/sessions").json()
        assert set(sessions) == {"items", "total", "others", "page", "size"}
        assert sessions["size"] == 10 and sessions["total"] == 23
        assert sessions["items"][0]["familyId"] == "cur" and sessions["items"][0]["current"]
        access = client.get("/auth/me/access", params={"page": 2}).json()
        assert access["size"] == 20 and access["page"] == 2 and len(access["items"]) == 20
        for path in ("/auth/me/sessions", "/auth/me/access"):
            assert client.get(path, params={"page": 0}).status_code == 422
            assert client.get(path, params={"size": 0}).status_code == 422
            assert client.get(path, params={"size": 101}).status_code == 422
