"""「문의·신고」: my reports a page at a time past the old 200, and only the unread ones."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.models.issues import IssueReply, IssueReport
from app.models.user import User, utcnow
from app.routers import issues


@pytest.fixture
async def client():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (User, IssueReport, IssueReply)])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    me = User(id="me", email="me@example.test", name="나", password_hash="x")
    other = User(id="other", email="o@example.test", name="남", password_hash="x")
    start = utcnow() - timedelta(days=30)
    async with local() as db:
        db.add_all([me, other])
        for n in range(230):
            # every tenth answered by an admin after I last looked; every 20th seen since
            answered = start + timedelta(hours=n, minutes=30) if n % 10 == 0 else None
            seen = start + timedelta(hours=n, minutes=45) if n % 20 == 0 else None
            db.add(IssueReport(id=f"r{n:031d}", user_id="me", body=f"신고 {n}",
                               created_at=start + timedelta(hours=n),
                               admin_updated_at=answered, user_seen_at=seen))
        db.add(IssueReport(id="x" * 32, user_id="other", body="남의 신고",
                           admin_updated_at=utcnow()))
        db.add(IssueReply(report_id=f"r{229:031d}", author_role="admin", author_name="관리자",
                          body="확인했습니다"))
        await db.commit()

    app = FastAPI()
    app.include_router(issues.router)

    async def db_session():
        async with local() as db:
            yield db

    app.dependency_overrides[get_session] = db_session
    app.dependency_overrides[current_user] = lambda: me
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        try:
            yield c
        finally:
            await engine.dispose()


async def _walk(client, **params) -> tuple[list[dict], list[dict]]:
    items, pages, cursor = [], [], None
    while True:
        body = (await client.get("/issues/page", params={
            **params, **({"cursor": cursor} if cursor else {})})).json()
        pages.append(body)
        items.extend(body["items"])
        cursor = body["next"]
        if cursor is None:
            return items, pages


async def test_my_reports_page_to_the_first_and_the_unread_filter_counts_as_the_badge(client):
    newest = [f"r{n:031d}" for n in reversed(range(230))]
    mine, pages = await _walk(client, limit=100)
    assert [r["id"] for r in mine] == newest
    assert [len(p["items"]) for p in pages] == [100, 100, 30]
    badge = (await client.get("/issues/unread")).json()["count"]
    # answered (every 10th), less those seen after the answer (every 20th)
    assert badge == 11
    assert {p["total"] for p in pages} == {230} and {p["unread"] for p in pages} == {badge}
    assert mine[0]["replies"] == 1 and mine[0]["body"] == "신고 229"

    unread, pages = await _walk(client, unread="true", limit=5)
    assert [r["id"] for r in unread] == [f"r{n:031d}" for n in reversed(range(230))
                                         if n % 10 == 0 and n % 20 != 0]
    assert all(r["unread"] for r in unread)
    assert {p["total"] for p in pages} == {badge} and {p["unread"] for p in pages} == {badge}

    # the older list is unchanged, still capped
    assert len((await client.get("/issues")).json()) == 200
    for bad in ({"cursor": "nope"}, {"limit": 0}, {"limit": 201}):
        assert (await client.get("/issues/page", params=bad)).status_code == 422
