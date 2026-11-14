"""Long lists reach their end: the file box, the sidebar and a bot's conversations page by
cursor (or limit) with a total, past what the plain lists used to cap (real HTTP, SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import test_publishing
from sqlmodel import select
from test_publishing import _publish

from app.models.chat import ChatSession, Message, Role
from app.models.publishing import Publication
from app.models.user import AuditEvent, utcnow
from app.models.workspace import Agent, StoredFile

world = test_publishing.world


async def _walk(client, path: str, **params) -> tuple[list[dict], list[dict]]:
    """Every page of `path`; the ids in order and each page's body."""
    items, pages, cursor = [], [], None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(path, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        pages.append(body)
        items.extend(body["items"])
        cursor = body["next"]
        if cursor is None:
            return items, pages
        assert len(pages) < 50


async def test_the_file_box_pages_with_counts_and_leaves_agents_knowledge_out(world):
    start = utcnow() - timedelta(days=1)
    async with world.local() as db:
        db.add(Agent(id="copy-1", owner_id="owner", name="공개 사본", snapshot_of="agent-1"))
        for n in range(7):
            db.add(StoredFile(id=f"u{n:031d}", user_id="owner", name=f"회의록 {n}.txt",
                              created_at=start + timedelta(minutes=n)))
        for n in range(3):
            db.add(StoredFile(id=f"g{n:031d}", user_id="owner", name=f"차트 {n}.png",
                              origin="generated", session_id="s-1",
                              created_at=start + timedelta(minutes=10 + n)))
        # a published copy's knowledge and someone else's file never show
        db.add(StoredFile(id="c" * 32, user_id="owner", agent_id="copy-1", name="사본 자료.pdf"))
        db.add(StoredFile(id="o" * 32, user_id="other", name="남의 파일.txt"))
        await db.commit()
    world.as_("owner")

    # everything `/files` lists: 10 here, the agent's 학사규정.pdf and the 급여대장.xlsx
    everything, pages = await _walk(world.client, "/files/page", limit=4)
    plain = (await world.client.get("/files")).json()
    assert [f["id"] for f in everything] == [f["id"] for f in plain]
    assert len(pages) == 3 and {p["total"] for p in pages} == {12}
    assert [len(p["items"]) for p in pages] == [4, 4, 4]
    assert pages[0]["counts"] == {"upload": 9, "generated": 3}
    assert set(pages[0]["items"][0]) >= {"id", "name", "origin", "createdAt", "sessionId"}

    # the file box: agents' knowledge out
    boxed, pages = await _walk(world.client, "/files/page", box="true", limit=50)
    assert "rules" not in {f["id"] for f in boxed} and len(boxed) == 11
    assert pages[0]["total"] == 11
    assert pages[0]["counts"] == {"upload": 8, "generated": 3, "working": 0}

    made, pages = await _walk(world.client, "/files/page", box="true", origin="generated",
                              limit=2)
    assert [f["id"] for f in made] == [f"g{n:031d}" for n in (2, 1, 0)]
    assert pages[0]["total"] == 3
    assert pages[0]["counts"] == {"upload": 8, "generated": 3, "working": 0}

    # name search, case and spacing aside; counts follow the search, origin aside
    found, pages = await _walk(world.client, "/files/page", box="true", q="  회의록 ",
                               origin="generated")
    assert found == [] and pages[0]["counts"] == {"upload": 7, "generated": 0, "working": 0}
    literal = (await world.client.get("/files/page", params={"q": "100%_"})).json()
    assert literal["total"] == 0
    in_session = (await world.client.get("/files/page", params={"session_id": "s-1"})).json()
    assert in_session["total"] == 3

    for bad in ({"cursor": "not-a-cursor"}, {"cursor": "fA"}, {"limit": 0}, {"limit": 201},
                {"origin": "stolen"}):
        response = await world.client.get("/files/page", params=bad)
        assert response.status_code == 422, (bad, response.text)


async def test_the_sidebar_takes_every_pin_and_a_limit_of_the_rest(world):
    start = utcnow() - timedelta(days=1)
    async with world.local() as db:
        for n in range(8):
            session = ChatSession(id=f"s{n:031d}", user_id="owner", title=f"대화 {n}",
                                  model="local/m", pinned=n in (1, 2),
                                  updated_at=start + timedelta(minutes=n),
                                  created_at=start)
            db.add(session)
            db.add(Message(session_id=session.id, role=Role.user, content=f"질문 {n}"))
        await db.commit()
    world.as_("owner")
    whole = (await world.client.get("/sessions")).json()
    assert [s["id"] for s in whole][:2] == [f"s{n:031d}" for n in (2, 1)]
    async with world.local() as db:
        # empty and old: not worth listing, so it never takes a place in the cut
        db.add(ChatSession(id="e" * 32, user_id="owner", title="빈 대화", model="local/m",
                           updated_at=start + timedelta(hours=2), created_at=start))
        await db.commit()

    cut = (await world.client.get("/sessions", params={"limit": 3})).json()
    assert [s["id"] for s in cut] == [f"s{n:031d}" for n in (2, 1, 7, 6, 5)]
    assert cut[2]["preview"] == "질문 7" and cut[2]["messageCount"] == 1
    assert [s["id"] for s in cut] == [s["id"] for s in whole][:5]
    for bad in (0, 501):
        assert (await world.client.get("/sessions", params={"limit": bad})).status_code == 422


async def test_a_bots_conversations_page_to_the_oldest_for_the_owner_and_the_admin(world):
    pub = await _publish(world)
    start = datetime(2026, 3, 1, 12, tzinfo=UTC)
    async with world.local() as db:
        row = await db.get(Publication, pub["id"])
        row.conversation_view = "off"
        db.add(row)
        for n in range(230):
            db.add(ChatSession(id=f"b{n:031d}", user_id="owner", publication_id=pub["id"],
                               title=f"문의 {n}", model="local/m",
                               # every third one sent to the maker; the rest only viewable
                               shared_at=start if n % 3 == 0 else None, viewable=True,
                               updated_at=start + timedelta(days=n), created_at=start))
        await db.commit()
    newest = [f"b{n:031d}" for n in reversed(range(230))]
    sent = [sid for sid in newest if int(sid[1:]) % 3 == 0]

    world.as_("owner")
    path = f"/publications/{pub['id']}/conversations/page"
    mine, pages = await _walk(world.client, path, limit=30)
    assert [c["id"] for c in mine] == sent and {p["total"] for p in pages} == {len(sent)}
    assert set(mine[0]) >= {"id", "title", "updatedAt", "turns", "visitor"}

    # 「대화 열람」 on: the viewable ones too, past the old 500-row scan
    async with world.local() as db:
        row = await db.get(Publication, pub["id"])
        row.conversation_view = "on"
        db.add(row)
        await db.commit()
    mine, pages = await _walk(world.client, path, limit=200)
    assert [c["id"] for c in mine] == newest and pages[0]["total"] == 230
    assert len(pages) == 2
    world.as_("other")
    assert (await world.client.get(path)).status_code == 404

    world.as_("admin")
    admin_path = f"/admin/publications/{pub['id']}/conversations/page"
    assert (await world.client.get(admin_path)).status_code == 422
    why = {"reason": "신고 접수 건 확인"}
    seen, pages = await _walk(world.client, admin_path, limit=100, **why)
    assert [c["id"] for c in seen] == newest and len(pages) == 3
    async with world.local() as db:
        audits = (await db.exec(select(AuditEvent).where(
            AuditEvent.action == "publication.admin_list"))).all()
    assert len(audits) == 3 and audits[0].event_metadata["reason"] == "신고 접수 건 확인"

    # by last-activity date, both ends inclusive (UTC days): day 200 .. day 205
    dated = (await world.client.get(admin_path, params={
        **why, "date_from": "2026-09-17", "date_to": "2026-09-22"})).json()
    assert [c["id"] for c in dated["items"]] == [f"b{n:031d}" for n in range(205, 199, -1)]
    assert dated["total"] == 6 and dated["next"] is None
    for bad in ({"date_from": "2026-13-01"}, {"date_from": "2026-09-22", "date_to": "2026-09-17"},
                {"cursor": "%%%"}, {"limit": 500}):
        response = await world.client.get(admin_path, params={**why, **bad})
        assert response.status_code == 422, (bad, response.text)
