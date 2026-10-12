"""관리자 › 사용자 reads one page at a time: the search first, then the tab, then the order;
tab counts follow the search, the cards above the list do not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User, UserRole, UserStatus
from app.routers import admin

_DDL = """
    CREATE TABLE users (
        id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
        monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
        litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
        litellm_key_issued_at DATETIME,
        avatar_color TEXT, allowed_models TEXT, preferences TEXT, created_at DATETIME,
        last_active_at DATETIME, email_verified_at DATETIME
    )
"""

T0 = datetime(2026, 10, 1, tzinfo=UTC)


async def _db() -> AsyncSession:
    """A user table holding 25 accounts: every fifth pending, every seventh suspended;
    credits and activity tie in pairs so the tie-breakers decide."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_DDL)
    db = AsyncSession(engine, expire_on_commit=False)
    for i in range(25):
        state = (
            UserStatus.pending
            if i % 5 == 0
            else UserStatus.suspended
            if i % 7 == 0
            else UserStatus.active
        )
        db.add(
            User(
                id=f"u{i:02d}",
                email=f"person{i:02d}@{'lab' if i % 2 else 'corp'}.example",
                name=f"사용자 {i:02d}",
                password_hash="x",
                role=UserRole.user,
                status=state,
                monthly_credits=1000,
                credits_used=(i // 2) * 10,
                cycle_resets_at=T0 + timedelta(days=31) if state != UserStatus.pending else None,
                created_at=T0 + timedelta(hours=i),
                last_active_at=None if i % 4 == 3 else T0 + timedelta(minutes=i // 2),
            )
        )
    await db.commit()
    return db


def _admin() -> User:
    return User(id="adm", email="admin@example.com", name="관리자", password_hash="x")


async def _page(db, **kw):
    params = {
        "tab": "all",
        "q": "",
        "sort": "status",
        "order": "asc",
        "page": 1,
        "limit": 50,
    }
    params.update(kw)
    return await admin.list_users_page(_admin(), db, **params)


async def _all_ids(db, **kw) -> list[str]:
    """Every page of a listing, three at a time, joined."""
    ids: list[str] = []
    page = 1
    while True:
        out = await _page(db, page=page, limit=3, **kw)
        ids += [u.id for u in out.items]
        if page * 3 >= out.total:
            return ids
        page += 1


@pytest.mark.asyncio
async def test_each_column_sorts_both_ways_and_pages_never_repeat_or_skip():
    db = await _db()
    for sort in ("name", "status", "credits", "last_active"):
        for order in ("asc", "desc"):
            whole = [u.id for u in (await _page(db, sort=sort, order=order, limit=200)).items]
            paged = await _all_ids(db, sort=sort, order=order)
            assert paged == whole, (sort, order)
            assert sorted(paged) == [f"u{i:02d}" for i in range(25)]

    by_name = [u.name for u in (await _page(db, sort="name")).items]
    assert by_name == sorted(by_name)
    assert [u.name for u in (await _page(db, sort="name", order="desc")).items] == by_name[::-1]

    credits = (await _page(db, sort="credits", order="desc")).items
    used = [u.credits_used for u in credits]
    assert used == sorted(used, reverse=True)
    # A tie in credits falls back to the name.
    tied = [u for u in credits if u.credits_used == 50]
    assert [u.name for u in tied] == sorted(u.name for u in tied)

    for order in ("asc", "desc"):
        seen = (await _page(db, sort="last_active", order=order)).items
        never = [u for u in seen if u.last_active_at is None]
        # Accounts never active come last whichever way the column runs.
        assert seen[-len(never) :] == never
        stamps = [u.last_active_at for u in seen if u.last_active_at is not None]
        assert stamps == sorted(stamps, reverse=order == "desc")

    states = [u.status for u in (await _page(db, sort="status")).items]
    rank = {UserStatus.pending: 0, UserStatus.active: 1, UserStatus.suspended: 2}
    assert [rank[s] for s in states] == sorted(rank[s] for s in states)
    states_desc = [u.status for u in (await _page(db, sort="status", order="desc")).items]
    assert states_desc[0] == UserStatus.suspended and states_desc[-1] == UserStatus.pending


@pytest.mark.asyncio
async def test_the_default_order_is_the_old_one_pending_first_newest_first():
    db = await _db()
    out = await _page(db)
    rank = {"pending": 0, "active": 1, "suspended": 2}
    old = sorted(out.items, key=lambda u: (rank[u.status], -u.created_at.timestamp()))
    assert [u.id for u in out.items] == [u.id for u in old]


@pytest.mark.asyncio
async def test_paging_reports_the_window_and_the_total():
    db = await _db()
    first = await _page(db, sort="name", limit=10)
    third = await _page(db, sort="name", limit=10, page=3)
    beyond = await _page(db, sort="name", limit=10, page=4)
    assert (first.total, first.page, first.limit, len(first.items)) == (25, 1, 10, 10)
    assert [u.id for u in third.items] == [f"u{i:02d}" for i in range(20, 25)]
    assert beyond.items == [] and beyond.total == 25


@pytest.mark.asyncio
async def test_tab_counts_follow_the_search_and_the_cards_do_not():
    db = await _db()
    out = await _page(db)
    # pending: 0,5,10,15,20; suspended: 7,14,21 (and not a multiple of five)
    assert out.counts.model_dump() == {"all": 25, "pending": 5, "active": 17, "suspended": 3}
    assert out.summary.users == 25 and out.summary.pending == 5
    assert out.summary.credits_granted == 25_000
    assert out.summary.credits_used == sum((i // 2) * 10 for i in range(25))
    assert out.summary.next_reset is not None

    # "lab" is in the odd addresses only.
    lab = await _page(db, q="LAB")
    assert lab.total == 12 and all("lab" in u.email for u in lab.items)
    assert lab.counts.model_dump() == {"all": 12, "pending": 2, "active": 8, "suspended": 2}
    assert lab.summary.users == 25 and lab.summary.pending == 5

    pending_lab = await _page(db, q="lab", tab="pending")
    assert pending_lab.total == 2 and {u.id for u in pending_lab.items} == {"u05", "u15"}
    # The search reaches names too, and its own wildcard characters are only characters.
    assert (await _page(db, q="사용자 1")).total == 10
    assert (await _page(db, q="%")).total == 0
    assert (await _page(db, q="_")).total == 0
    assert (await _page(db, q="   ")).total == 25


@pytest.mark.asyncio
async def test_the_whole_list_endpoint_keeps_its_shape():
    db = await _db()
    rows = await admin.list_users(_admin(), db)
    assert isinstance(rows, list) and len(rows) == 25
    assert rows[0].status == UserStatus.pending
