"""Published agents opened outside: by link or listed for everyone, once an administrator
approved it. A visitor without an account is a signed cookie: they reach only an approved
version, only their own conversations, nothing of the owner's, and within limits counted by
address. Refusing, closing or revoking cuts them off with their next request.

Real HTTP and SQLite on the fixture of `test_publishing`; the model is its fake agent loop."""

from __future__ import annotations

from datetime import timedelta

import pytest
import test_publishing
from sqlmodel import select
from test_publishing import _publish

from app.core.deps import current_user
from app.core.security import create_access_token
from app.models.chat import ChatSession, Message
from app.models.publishing import Publication, PublicationVersion, PublicationVisitor
from app.models.user import AuditEvent, CreditLedger, User, UserStatus, utcnow
from app.models.workspace import Agent
from app.routers import publications
from app.services import publishing

# The fixture of `test_publishing`, registered here under its own name.
world = test_publishing.world

_CAP = {"limits": {"creditsPerDay": 5000}}


def _anon(w) -> None:
    """The next calls come from a browser without an account."""
    w.user = None


def _fresh_browser(w) -> None:
    """Another browser: no account, no visitor cookie."""
    w.user = None
    w.client.cookies.clear()


async def _approve(w, pub_id: str, reason: str = "") -> dict:
    w.as_("admin")
    done = await w.client.post(f"/admin/publications/{pub_id}/review",
                               json={"decision": "approve", "reason": reason})
    assert done.status_code == 200, done.text
    return done.json()


async def _open_by_link(w) -> dict:
    pub = await _publish(w, visibility="link", **_CAP)
    await _approve(w, pub["id"])
    return pub


async def _visit(w, slug: str, text: str = "휴학은 언제까지 신청해요?") -> tuple[str, str]:
    """Opens the bot without an account, agrees to the notice and asks once."""
    _anon(w)
    page = await w.client.get(f"/b/{slug}")
    assert page.status_code == 200, page.text
    if not page.json()["consented"]:
        agreed = await w.client.post(f"/b/{slug}/consent",
                                     json={"version": page.json()["consentVersion"]})
        assert agreed.status_code == 204, agreed.text
    made = await w.client.post(f"/b/{slug}/conversations")
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    reply = await w.client.post(f"/b/{slug}/conversations/{sid}/messages",
                                json={"content": text})
    assert reply.status_code == 200, reply.text
    return sid, reply.text


# ── the request and its review ─────────────────────────────────────────


async def test_a_link_waits_for_an_administrator_and_needs_a_daily_credit_cap(world):
    world.as_("owner")
    refused = await world.client.post("/publications",
                                      json={"agentId": "agent-1", "visibility": "link"})
    assert refused.status_code == 422 and refused.json()["detail"] == "credits_cap_required"
    pub = await _publish(world, visibility="link", **_CAP)
    # Members have it at once; the wider audience waits.
    assert pub["visibility"] == "internal" and pub["status"] == "approved"
    assert pub["reviewState"] == "pending" and pub["requestedVisibility"] == "link"
    assert pub["limits"]["creditsPerDay"] == 5000
    _fresh_browser(world)
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404
    world.as_("member")
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 200
    world.as_("admin")
    assert (await world.client.get("/publications/attention")).json()["reviews"] == 1
    queue = (await world.client.get("/admin/publications?review=pending")).json()
    assert [p["id"] for p in queue] == [pub["id"]]
    review = (await world.client.get(f"/admin/publications/{pub['id']}/review")).json()
    assert review["agent"]["instructions"] == "규정에서 찾아 답한다."
    assert review["agent"]["contact"].startswith("학사지원팀")
    assert review["knowledge"][0]["name"] == "학사규정.pdf"
    assert review["owner"]["email"] == "owner@example.test"
    assert review["version"]["number"] == 1 and review["version"]["approvedAt"] is None


async def test_approval_opens_the_bot_to_people_without_an_account(world):
    pub = await _publish(world, visibility="link", **_CAP)
    approved = await _approve(world, pub["id"])
    assert approved["visibility"] == "link" and approved["reviewState"] == ""
    _fresh_browser(world)
    page = await world.client.get(f"/b/{pub['slug']}")
    assert page.status_code == 200
    body = page.json()
    # The bot, not the person behind it; a cookie names this browser from now on.
    assert body["ownerName"] == "" and body["signedIn"] is False and body["consented"] is False
    assert page.headers["X-Robots-Tag"] == "noindex"
    cookie = page.cookies.get(publishing.VISITOR_COOKIE)
    assert cookie and publishing.visitor_from_cookie(cookie)
    assert "httponly" in page.headers["set-cookie"].lower()
    assert "samesite=lax" in page.headers["set-cookie"].lower()
    sid, stream = await _visit(world, pub["slug"])
    assert "휴학은 학기 개시일 전까지" in stream
    (call,) = world.calls
    assert call["model"] == "strict-local/m" and call["strict"] is True
    sent = str(call["messages"])
    for private in ("대표 급여", "김철수", "급여대장", "회계팀"):
        assert private not in sent
    async with world.local() as db:
        session = await db.get(ChatSession, sid)
        assert session.user_id == "owner" and session.publication_id == pub["id"]
        visitor = await db.get(PublicationVisitor, session.visitor_id)
        assert visitor.user_id is None and visitor.anon_id
        version = await db.get(PublicationVersion, session.publication_version_id)
        assert version.approved_at is not None and version.approved_by == "admin"
        audited = {e.action for e in (await db.exec(select(AuditEvent))).all()}
        assert {"publication.requested", "publication.reviewed"} <= audited
    # The owner is told, once.
    world.as_("owner")
    assert (await world.client.get("/publications/attention")).json()["bots"] == 1
    await world.client.get(f"/publications/{pub['id']}")
    assert (await world.client.get("/publications/attention")).json()["bots"] == 0


async def test_a_link_is_listed_nowhere_and_an_open_bot_is_listed_for_everyone(world):
    pub = await _open_by_link(world)
    _fresh_browser(world)
    assert (await world.client.get("/public/bots")).json() == []
    world.as_("member")
    assert pub["slug"] not in [b["slug"] for b in (await world.client.get("/bots")).json()]
    world.as_("owner")
    asked = await world.client.post(f"/publications/{pub['id']}/scope",
                                    json={"visibility": "external"})
    assert asked.json()["reviewState"] == "pending" and asked.json()["visibility"] == "link"
    await _approve(world, pub["id"])
    _fresh_browser(world)
    listed = (await world.client.get("/public/bots")).json()
    assert [b["slug"] for b in listed] == [pub["slug"]] and "ownerName" not in listed[0]
    page = await world.client.get(f"/b/{pub['slug']}")
    assert "X-Robots-Tag" not in page.headers
    world.as_("member")
    assert pub["slug"] in [b["slug"] for b in (await world.client.get("/bots")).json()]
    # Narrowing is the owner's to do, at once.
    world.as_("owner")
    narrowed = await world.client.post(f"/publications/{pub['id']}/scope",
                                       json={"visibility": "internal"})
    assert narrowed.json()["visibility"] == "internal"
    _fresh_browser(world)
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404
    assert (await world.client.get("/public/bots")).json() == []


async def test_a_refusal_keeps_the_bot_for_members_and_tells_the_owner_why(world):
    pub = await _publish(world, visibility="external", **_CAP)
    world.as_("admin")
    bare = await world.client.post(f"/admin/publications/{pub['id']}/review",
                                   json={"decision": "reject"})
    assert bare.status_code == 422
    refused = await world.client.post(
        f"/admin/publications/{pub['id']}/review",
        json={"decision": "reject", "reason": "자료에 개인 연락처가 있습니다"})
    assert refused.json()["reviewState"] == "rejected"
    again = await world.client.post(f"/admin/publications/{pub['id']}/review",
                                    json={"decision": "approve"})
    assert again.status_code == 409
    world.as_("owner")
    mine = (await world.client.get(f"/publications/{pub['id']}")).json()
    assert mine["visibility"] == "internal"
    assert mine["reviewNote"] == "자료에 개인 연락처가 있습니다"
    _fresh_browser(world)
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404
    world.as_("member")
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 200


async def test_a_new_version_waits_while_the_approved_one_answers(world):
    pub = await _open_by_link(world)
    async with world.local() as db:
        agent = await db.get(Agent, "agent-1")
        agent.system_prompt = "검토 전 새 지시"
        db.add(agent)
        await db.commit()
    world.as_("owner")
    waiting = (await world.client.post(f"/publications/{pub['id']}/versions",
                                       json={"note": "둘째"})).json()
    v1 = next(v for v in waiting["versions"] if v["number"] == 1)
    v2 = next(v for v in waiting["versions"] if v["number"] == 2)
    assert waiting["activeVersionId"] == v1["id"] and waiting["pendingVersionId"] == v2["id"]
    assert waiting["reviewState"] == "pending" and v2["pending"] and not v2["active"]
    await _visit(world, pub["slug"])
    assert "검토 전 새 지시" not in str(world.calls[-1]["messages"])
    # Rolling forward to a version nobody approved waits too.
    world.as_("owner")
    rolled = (await world.client.post(
        f"/publications/{pub['id']}/versions/{v2['id']}/activate")).json()
    assert rolled["activeVersionId"] == v1["id"]
    await _approve(world, pub["id"])
    await _visit(world, pub["slug"])
    assert "검토 전 새 지시" in str(world.calls[-1]["messages"])
    # Back to an approved version: at once.
    world.as_("owner")
    back = (await world.client.post(
        f"/publications/{pub['id']}/versions/{v1['id']}/activate")).json()
    assert back["activeVersionId"] == v1["id"] and back["pendingVersionId"] is None


async def test_closing_the_outside_audience_cuts_visitors_off_at_once(world):
    pub = await _open_by_link(world)
    sid, _ = await _visit(world, pub["slug"])
    world.as_("admin")
    short = await world.client.post(f"/admin/publications/{pub['id']}/withdraw",
                                     json={"reason": "x"})
    assert short.status_code == 422
    withdrawn = await world.client.post(f"/admin/publications/{pub['id']}/withdraw",
                                        json={"reason": "외부 공개 기준에 맞지 않습니다"})
    assert withdrawn.json()["visibility"] == "internal"
    _anon(world)
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404
    assert (await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                    json={"content": "또 질문"})).status_code == 404
    assert (await world.client.get(f"/b/{pub['slug']}/conversations/{sid}")).status_code == 404
    world.as_("member")
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 200
    # Opened again, then the whole bot closed: nobody but its owner reaches it.
    world.as_("owner")
    await world.client.post(f"/publications/{pub['id']}/scope", json={"visibility": "link"})
    await _approve(world, pub["id"])
    world.as_("admin")
    await world.client.post(f"/admin/publications/{pub['id']}/status",
                            json={"status": "revoked", "reason": "남용 신고 확인"})
    _anon(world)
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404
    world.as_("member")
    assert (await world.client.get(f"/b/{pub['slug']}")).status_code == 404


async def test_an_unapproved_version_never_answers_without_an_account(world):
    pub = await _open_by_link(world)
    async with world.local() as db:
        row = await db.get(Publication, pub["id"])
        version = await db.get(PublicationVersion, row.active_version_id)
        version.approved_at = None
        db.add(version)
        await db.commit()
    _fresh_browser(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    refused = await world.client.post(f"/b/{pub['slug']}/conversations")
    assert refused.status_code == 409 and refused.json()["detail"] == "bot_paused"
    assert world.calls == []


# ── what a visitor without an account reaches ──────────────────────────


async def test_a_visitor_without_an_account_reaches_only_their_own_conversations(world):
    pub = await _open_by_link(world)
    sid, _ = await _visit(world, pub["slug"])
    first_cookie = world.client.cookies.get(publishing.VISITOR_COOKIE)
    async with world.local() as db:
        answer = (await db.exec(select(Message).where(Message.session_id == sid,
                                                      Message.role == "assistant"))).first()
    _fresh_browser(world)
    await world.client.get(f"/b/{pub['slug']}")
    slug = pub["slug"]
    assert (await world.client.get(f"/b/{slug}/conversations")).json() == []
    for method, path, payload in (
            ("GET", f"/b/{slug}/conversations/{sid}", None),
            ("DELETE", f"/b/{slug}/conversations/{sid}", None),
            ("POST", f"/b/{slug}/conversations/{sid}/messages", {"content": "남의 대화"}),
            ("POST", f"/b/{slug}/conversations/{sid}/share", None),
            ("PATCH", f"/b/{slug}/messages/{answer.id}/rating", {"rating": "down"})):
        got = await world.client.request(method, path, json=payload)
        assert got.status_code in (404, 409), (method, path, got.status_code)
        assert got.status_code != 200
    # A forged cookie — the first visitor's id under another signature — is nobody.
    anon_id = publishing.visitor_from_cookie(first_cookie)
    world.client.cookies.clear()
    world.client.cookies.set(publishing.VISITOR_COOKIE, f"{anon_id}.forged")
    assert (await world.client.get(f"/b/{slug}/conversations")).json() == []
    assert (await world.client.get(f"/b/{slug}/conversations/{sid}")).status_code == 404
    refused = await world.client.post(f"/b/{slug}/consent", json={"version": 1})
    assert refused.status_code == 409 and refused.json()["detail"] == "visitor_cookie_required"
    # Nor does a member reach it.
    world.as_("member")
    assert (await world.client.get(f"/b/{slug}/conversations/{sid}")).status_code == 404
    # The first browser still does.
    world.client.cookies.clear()
    world.client.cookies.set(publishing.VISITOR_COOKIE, first_cookie)
    _anon(world)
    mine = await world.client.get(f"/b/{slug}/conversations/{sid}")
    assert mine.status_code == 200 and mine.json()["id"] == sid


async def test_without_an_account_none_of_the_app_is_reached(world):
    await _open_by_link(world)
    _fresh_browser(world)
    world.app.dependency_overrides.pop(current_user)
    for path in ("/files", "/agents", "/publications", f"/sessions/{'x' * 32}"):
        assert (await world.client.get(path)).status_code == 401, path


async def test_the_caller_is_a_member_only_with_a_live_token_of_an_active_account(world):
    from starlette.requests import Request

    def request(token: str) -> Request:
        header = (b"authorization", f"Bearer {token}".encode())
        return Request({"type": "http", "headers": [header]})

    async with world.local() as db:
        token, _ = create_access_token("member", "user")
        assert (await publications.bot_caller(request(token), db)).id == "member"
        assert await publications.bot_caller(request("not-a-token"), db) is None
        user = await db.get(User, "member")
        user.status = UserStatus.pending
        db.add(user)
        await db.commit()
        assert await publications.bot_caller(request(token), db) is None


def test_the_visitor_cookie_is_signed_and_the_address_is_not_kept():
    anon_id, value = publishing.new_visitor_cookie()
    assert publishing.visitor_from_cookie(value) == anon_id
    for forged in (anon_id, f"{anon_id}.", f"{anon_id}.AAAA", f"x{value}", "", "a.b"):
        assert publishing.visitor_from_cookie(forged) is None
    key = publishing.ip_key("203.0.113.7")
    assert "203" not in key and key == publishing.ip_key("203.0.113.7")
    assert key != publishing.ip_key("203.0.113.8")


# ── limits ─────────────────────────────────────────────────────────────


async def _limit(w, pub_id: str, **limits) -> None:
    async with w.local() as db:
        row = await db.get(Publication, pub_id)
        row.limits = {**(row.limits or {}), **limits}
        db.add(row)
        await db.commit()


async def test_an_address_is_limited_whatever_cookies_it_brings(world):
    pub = await _open_by_link(world)
    await _limit(world, pub["id"], perIpPerMinute=2)
    await _visit(world, pub["slug"])
    _fresh_browser(world)
    await _visit(world, pub["slug"])
    _fresh_browser(world)
    _anon(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    sid = (await world.client.post(f"/b/{pub['slug']}/conversations")).json()["id"]
    refused = await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                      json={"content": "세 번째"})
    assert refused.status_code == 429 and refused.json()["detail"] == "visitor_rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1
    # A member behind the same address is not counted by it.
    world.as_("member")
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    msid = (await world.client.post(f"/b/{pub['slug']}/conversations")).json()["id"]
    assert (await world.client.post(f"/b/{pub['slug']}/conversations/{msid}/messages",
                                    json={"content": "회원 질문"})).status_code == 200


async def test_new_visitors_from_one_address_are_bounded_per_day(world, monkeypatch):
    monkeypatch.setattr(publishing, "ANON_VISITORS_PER_IP_DAY", 2)
    pub = await _open_by_link(world)
    for _ in range(2):
        _fresh_browser(world)
        page = (await world.client.get(f"/b/{pub['slug']}")).json()
        agreed = await world.client.post(f"/b/{pub['slug']}/consent",
                                         json={"version": page["consentVersion"]})
        assert agreed.status_code == 204
    _fresh_browser(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    refused = await world.client.post(f"/b/{pub['slug']}/consent",
                                      json={"version": page["consentVersion"]})
    assert refused.status_code == 429 and refused.json()["detail"] == "visitor_daily_limit"


async def test_the_owners_daily_credit_cap_stops_the_bot_for_the_day(world):
    pub = await _open_by_link(world)
    await _limit(world, pub["id"], creditsPerDay=100)
    async with world.local() as db:
        db.add(CreditLedger(user_id="owner", delta=-60, reason="chat", publication_id=pub["id"]))
        # Yesterday's spend and the owner's own chats do not count.
        db.add(CreditLedger(user_id="owner", delta=-500, reason="chat", publication_id=pub["id"],
                            created_at=utcnow() - timedelta(days=2)))
        db.add(CreditLedger(user_id="owner", delta=-900, reason="chat"))
        await db.commit()
    await _visit(world, pub["slug"])
    async with world.local() as db:
        db.add(CreditLedger(user_id="owner", delta=-60, reason="chat", publication_id=pub["id"]))
        await db.commit()
    _fresh_browser(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    sid = (await world.client.post(f"/b/{pub['slug']}/conversations")).json()["id"]
    refused = await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                      json={"content": "오늘 또"})
    assert refused.status_code == 429 and refused.json()["detail"] == "daily_credits_reached"
    # The cap stays while the bot is open outside.
    world.as_("owner")
    dropped = await world.client.patch(f"/publications/{pub['id']}", json={"limits": {}})
    assert dropped.status_code == 422 and dropped.json()["detail"] == "credits_cap_required"


async def test_a_long_question_is_refused_before_it_runs(world):
    pub = await _open_by_link(world)
    await _limit(world, pub["id"], maxInputChars=20)
    _fresh_browser(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["maxInputChars"] == 20
    await world.client.post(f"/b/{pub['slug']}/consent", json={"version": page["consentVersion"]})
    sid = (await world.client.post(f"/b/{pub['slug']}/conversations")).json()["id"]
    refused = await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                      json={"content": "가" * 21})
    assert refused.status_code == 422 and world.calls == []


# ── the owner blocks a visitor ─────────────────────────────────────────


async def test_the_owner_blocks_a_visitor_from_a_conversation_they_sent(world):
    pub = await _open_by_link(world)
    sid, _ = await _visit(world, pub["slug"])
    assert (await world.client.post(
        f"/b/{pub['slug']}/conversations/{sid}/share")).status_code == 200
    world.as_("owner")
    (row,) = (await world.client.get(f"/publications/{pub['id']}/conversations")).json()
    assert row["visitor"]["member"] is False and row["visitor"]["blocked"] is False
    assert set(row["visitor"]) == {"id", "label", "member", "blocked", "blockedAt"}
    blocked = await world.client.post(f"/publications/{pub['id']}/conversations/{sid}/block")
    assert blocked.json()["blocked"] is True
    listed = (await world.client.get(f"/publications/{pub['id']}/visitors")).json()
    assert [v["id"] for v in listed] == [row["visitor"]["id"]]
    _anon(world)
    page = (await world.client.get(f"/b/{pub['slug']}")).json()
    assert page["blocked"] is True
    refused = await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                      json={"content": "다시"})
    assert refused.status_code == 403 and refused.json()["detail"] == "visitor_blocked"
    world.as_("owner")
    await world.client.delete(f"/publications/{pub['id']}/visitors/{row['visitor']['id']}/block")
    _anon(world)
    assert (await world.client.post(f"/b/{pub['slug']}/conversations/{sid}/messages",
                                    json={"content": "다시"})).status_code == 200


async def test_the_owner_cannot_block_from_a_conversation_they_may_not_read(world):
    pub = await _open_by_link(world)
    sid, _ = await _visit(world, pub["slug"])
    world.as_("owner")
    refused = await world.client.post(f"/publications/{pub['id']}/conversations/{sid}/block")
    assert refused.status_code == 404
    world.as_("other")
    assert (await world.client.get(f"/publications/{pub['id']}/visitors")).status_code == 404


@pytest.mark.parametrize("visibility", ["link", "external"])
async def test_pending_or_unknown_bots_read_the_same_to_a_visitor_without_an_account(
        world, visibility):
    pub = await _publish(world, visibility=visibility, **_CAP)
    _fresh_browser(world)
    pending = await world.client.get(f"/b/{pub['slug']}")
    unknown = await world.client.get("/b/no-such-bot-1234")
    assert pending.status_code == unknown.status_code == 404
    assert pending.json() == unknown.json()


async def test_a_visitor_without_an_account_is_forgotten_with_their_conversations(world):
    pub = await _open_by_link(world)
    sid, _ = await _visit(world, pub["slug"])
    gone = utcnow() - timedelta(days=31)
    async with world.local() as db:
        session = await db.get(ChatSession, sid)
        visitor = await db.get(PublicationVisitor, session.visitor_id)
        session.updated_at, visitor.last_seen_at = gone, gone
        db.add_all([session, visitor])
        await db.commit()
        await publishing.sweep(db)
        assert await db.get(ChatSession, sid) is None
        assert (await db.exec(select(PublicationVisitor).where(
            PublicationVisitor.publication_id == pub["id"]))).all() == []
