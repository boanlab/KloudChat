"""Web push: devices subscribe and leave, notices go out encrypted with VAPID, gone devices are
removed, retries back off, the per-user rate limit and event switches hold, and a notice never
carries what was written (internal material shows only generic text unless allowed)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import ChatSession, Message, Role
from app.models.push import PushSent, PushSubscription
from app.models.settings import SystemSetting
from app.models.user import CreditLedger, User, UserRole, UserStatus
from app.models.workspace import Artifact, ArtifactKind
from app.routers import push as push_router
from app.schemas.auth import Preferences
from app.services import credits, push, settings_store
from app.services import webpush_crypto as wc

ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-1"


class Device:
    """A browser's side of a subscription: keys to decrypt what the push service got."""

    def __init__(self, endpoint: str = ENDPOINT):
        self.private, self.public = wc.generate_keypair()
        self.auth = wc.b64u_encode(b"0123456789abcdef")
        self.endpoint = endpoint

    def read(self, body: bytes) -> dict:
        key = wc.private_key_from_raw(wc.b64u_decode(self.private))
        return json.loads(wc.decrypt(body, key, self.auth))

    def row(self, user_id: str = "me") -> PushSubscription:
        return PushSubscription(user_id=user_id, endpoint=self.endpoint, p256dh=self.public,
                                auth=self.auth, device="Chrome · Linux")


@pytest.fixture
async def stack(monkeypatch):
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
            metadata.tables[m.__tablename__] for m in (
                User, ChatSession, Message, Artifact, PushSubscription, PushSent,
                SystemSetting, CreditLedger)])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(core_db, "SessionLocal", local)
    monkeypatch.setattr(settings_store, "SessionLocal", local)
    monkeypatch.setattr(settings, "vapid_private_key", "")
    monkeypatch.setattr(settings, "vapid_public_key", "")
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@example.test")
    monkeypatch.setattr(settings, "push_backoff_sec", 0.0)
    monkeypatch.setattr(settings, "push_per_minute", 6)
    push._keys.clear()
    settings_store.invalidate()

    async with local() as db:
        db.add_all([
            User(id="me", email="me@example.test", name="나", password_hash="x",
                 status=UserStatus.active, monthly_credits=1000),
            User(id="admin", email="a@example.test", name="관리자", password_hash="x",
                 status=UserStatus.active, role=UserRole.admin),
        ])
        db.add(ChatSession(id="s1", user_id="me", title="분기 보고"))
        db.add(Message(session_id="s1", role=Role.assistant,
                       content="매출 기밀 숫자 4,321억"))
        db.add(ChatSession(id="s2", user_id="me", title="내부 감사", project_id="p1"))
        await db.commit()

    app = FastAPI()
    app.include_router(push_router.router)

    async def session():
        async with local() as db:
            yield db

    me = User(id="me", email="me@example.test", name="나", password_hash="x",
              status=UserStatus.active)
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: me
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test",
                               headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                        "Chrome/140.0 Safari/537.36"}) as client:
            yield client, local
    finally:
        push._keys.clear()
        await engine.dispose()


def mock_push(monkeypatch, *answers: int) -> list[httpx.Request]:
    """Push service answering each request with the next status (the last one repeats)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        code = answers[min(len(seen) - 1, len(answers) - 1)]
        return httpx.Response(code)

    monkeypatch.setattr(push, "_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


async def test_a_device_subscribes_is_listed_and_leaves(stack):
    client, local = stack
    config = (await client.get("/push/config")).json()
    assert len(wc.b64u_decode(config["publicKey"])) == 65
    assert config["events"]["job_done"] is True and config["events"]["issue"] is False
    device = Device()
    made = await client.post("/push/subscriptions", json={
        "endpoint": device.endpoint, "keys": {"p256dh": device.public, "auth": device.auth}})
    assert made.status_code == 201, made.text
    assert made.json()["device"] == "Chrome · Linux"
    # the same browser again: one row
    again = await client.post("/push/subscriptions", json={
        "endpoint": device.endpoint, "keys": {"p256dh": device.public, "auth": device.auth}})
    assert again.json()["id"] == made.json()["id"]
    listed = (await client.get("/push/subscriptions")).json()
    assert [d["id"] for d in listed] == [made.json()["id"]]
    assert "endpoint" not in listed[0]
    assert (await client.post("/push/unsubscribe", json={"endpoint": device.endpoint})
            ).status_code == 204
    assert (await client.get("/push/subscriptions")).json() == []


async def test_a_removed_device_is_gone_and_others_cannot_remove_mine(stack):
    client, local = stack
    async with local() as db:
        other = Device("https://fcm.googleapis.com/fcm/send/other").row("admin")
        db.add(other)
        await db.commit()
    assert (await client.delete(f"/push/subscriptions/{other.id}")).status_code == 404
    device = Device()
    made = (await client.post("/push/subscriptions", json={
        "endpoint": device.endpoint,
        "keys": {"p256dh": device.public, "auth": device.auth}})).json()
    assert (await client.delete(f"/push/subscriptions/{made['id']}")).status_code == 204
    assert (await client.get("/push/subscriptions")).json() == []


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/x",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost/push",
    "https://evil.example/fcm.googleapis.com",
    "https://fcm.googleapis.com.evil.example/x",
])
async def test_an_endpoint_outside_the_push_services_is_refused(stack, endpoint):
    client, _ = stack
    device = Device(endpoint)
    made = await client.post("/push/subscriptions", json={
        "endpoint": endpoint, "keys": {"p256dh": device.public, "auth": device.auth}})
    assert made.status_code == 422
    assert made.json()["detail"] == "push_endpoint_not_allowed"


async def test_bad_keys_are_refused(stack):
    client, _ = stack
    made = await client.post("/push/subscriptions", json={
        "endpoint": ENDPOINT, "keys": {"p256dh": "A" * 87, "auth": "B" * 22}})
    assert made.status_code == 422


async def test_the_vapid_pair_is_made_once_and_sealed(stack, monkeypatch):
    _, local = stack
    private, public = await push.vapid_keys()
    async with local() as db:
        row = await db.get(SystemSetting, settings_store.PUSH_VAPID_PRIVATE)
    assert row.secret and row.value != private
    assert settings_store.decrypt_secret(row.value) == private
    push._keys.clear()
    assert await push.vapid_keys() == (private, public)
    # sealed under a key this process no longer holds: replaced, not a crash
    async with local() as db:
        row = await db.get(SystemSetting, settings_store.PUSH_VAPID_PRIVATE)
        row.value = "gAAAAAnot-openable"
        db.add(row)
        await db.commit()
    push._keys.clear()
    fresh, fresh_public = await push.vapid_keys()
    assert fresh != private and wc.public_from_private(fresh) == fresh_public
    pinned, pinned_public = wc.generate_keypair()
    monkeypatch.setattr(settings, "vapid_private_key", pinned)
    assert await push.vapid_keys() == (pinned, pinned_public)


async def test_a_notice_is_encrypted_signed_and_names_only_the_title(stack, monkeypatch):
    _, local = stack
    device = Device()
    async with local() as db:
        db.add(device.row())
        db.add(Artifact(id="a1", user_id="me", kind=ArtifactKind.deck, title="덱",
                        session_id="s1"))
        await db.commit()
        session = await db.get(ChatSession, "s1")
        seen = mock_push(monkeypatch, 201)
        await push.notify_job(db, session, "report", ok=True, artifact_id="a1")
    await push.drain()
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert int(request.headers["TTL"]) > 0
    auth = request.headers["Authorization"]
    assert auth.startswith("vapid t=") and f"k={(await push.vapid_keys())[1]}" in auth
    payload = device.read(request.content)
    assert payload == {"title": "슬라이드가 완성됐습니다", "body": "분기 보고",
                       "url": "/s/s1?artifact=a1", "tag": "job:s1"}
    assert "4,321" not in json.dumps(payload, ensure_ascii=False)
    async with local() as db:
        row = (await db.exec(select(PushSubscription))).one()
    assert row.last_sent_at is not None


async def test_internal_material_shows_generic_text_unless_allowed(stack, monkeypatch):
    _, local = stack
    device = Device()
    async with local() as db:
        db.add(device.row())
        await db.commit()
        seen = mock_push(monkeypatch, 201)
        await push.notify_job(db, await db.get(ChatSession, "s2"), "report", ok=False)
        await push.drain()
        assert device.read(seen[-1].content)["body"] == push.GENERIC_BODY
        assert device.read(seen[-1].content)["title"] == "보고서를 만들지 못했습니다"
        user = await db.get(User, "me")
        user.preferences = {"push_internal_titles": True}
        db.add(user)
        await db.commit()
        await push.notify_job(db, await db.get(ChatSession, "s2"), "report", ok=True)
    await push.drain()
    assert device.read(seen[-1].content)["body"] == "내부 감사"


async def test_a_gone_device_is_deleted(stack, monkeypatch):
    _, local = stack
    async with local() as db:
        db.add(Device().row())
        await db.commit()
    seen = mock_push(monkeypatch, 410)
    sent = await push.deliver("me", push.Notice(event="job_done", title="t", tag="x"))
    assert sent == 0 and len(seen) == 1
    async with local() as db:
        assert (await db.exec(select(PushSubscription))).all() == []


async def test_a_busy_push_service_is_retried_with_backoff(stack, monkeypatch):
    _, local = stack
    async with local() as db:
        db.add(Device().row())
        await db.commit()
    seen = mock_push(monkeypatch, 503, 429, 201)
    assert await push.deliver("me", push.Notice(event="job_done", title="t")) == 1
    assert len(seen) == 3
    seen = mock_push(monkeypatch, 500)
    assert await push.deliver("me", push.Notice(event="job_done", title="t")) == 0
    assert len(seen) == push.ATTEMPTS
    async with local() as db:
        # a failing service does not cost the device
        assert len((await db.exec(select(PushSubscription))).all()) == 1


async def test_a_refused_message_is_not_retried(stack, monkeypatch):
    _, local = stack
    async with local() as db:
        db.add(Device().row())
        await db.commit()
    seen = mock_push(monkeypatch, 400)
    assert await push.deliver("me", push.Notice(event="job_done", title="t")) == 0
    assert len(seen) == 1


async def test_the_rate_limit_holds_per_user(stack, monkeypatch):
    _, local = stack
    monkeypatch.setattr(settings, "push_per_minute", 2)
    async with local() as db:
        db.add(Device().row())
        await db.commit()
    seen = mock_push(monkeypatch, 201)
    for _ in range(4):
        await push.deliver("me", push.Notice(event="job_done", title="t"))
    assert len(seen) == 2


async def test_event_switches_and_defaults(stack, monkeypatch):
    _, local = stack
    async with local() as db:
        db.add(Device().row())
        await db.commit()
    seen = mock_push(monkeypatch, 201)
    # off by default
    assert await push.deliver("me", push.Notice(event="issue", title="t")) == 0
    # on by default
    assert await push.deliver("me", push.Notice(event="shared", title="t")) == 1
    async with local() as db:
        user = await db.get(User, "me")
        user.preferences = {"push_events": {"shared": False, "issue": True}}
        db.add(user)
        await db.commit()
    assert await push.deliver("me", push.Notice(event="shared", title="t")) == 0
    assert await push.deliver("me", push.Notice(event="issue", title="t")) == 1
    assert len(seen) == 2


def test_the_settings_accept_only_known_events():
    assert Preferences(push_events={"job_done": False}).push_events == {"job_done": False}
    with pytest.raises(ValueError):
        Preferences(push_events={"marketing": True})
    out = Preferences.model_validate({"pushEvents": {"credits": True},
                                      "pushInternalTitles": True})
    assert out.push_events == {"credits": True} and out.push_internal_titles


async def test_the_credit_warning_goes_once_per_cycle(stack, monkeypatch):
    _, local = stack
    async with local() as db:
        db.add(Device().row())
        user = await db.get(User, "me")
        user.preferences = {"push_events": {"credits": True}}
        db.add(user)
        await db.commit()
    seen = mock_push(monkeypatch, 201)
    for spend, heard in ((850, 0), (100, 1), (20, 1)):
        async with local() as db:
            user = await db.get(User, "me")
            credits.settle(db, user, spend, reason="chat.turn")
            await db.commit()
        await push.drain()
        assert len(seen) == heard
    # a second crossing in the same cycle (e.g. after an admin raised and lowered the
    # allowance) sends nothing
    assert await push.deliver("me", push.Notice(
        event="credits", title="t", once_key="credits:none")) == 0


async def test_a_published_bots_visitor_turn_sends_nothing(stack, monkeypatch):
    _, local = stack
    sent: list = []
    monkeypatch.setattr(push, "notify", lambda *a, **k: sent.append(a))
    async with local() as db:
        session = await db.get(ChatSession, "s1")
        session.publication_id = "pub1"
        await push.notify_job(db, session, "report", ok=True)
    assert sent == []


async def test_admins_hear_of_review_requests_but_not_the_one_who_asked(stack, monkeypatch):
    _, local = stack
    sent: list = []
    monkeypatch.setattr(push, "notify", lambda user_id, notice: sent.append(user_id))
    async with local() as db:
        await push.notify_admins(db, push.Notice(event="review", title="t"), but="me")
        await push.notify_admins(db, push.Notice(event="review", title="t"), but="admin")
    assert sent == ["admin"]


def test_device_labels():
    label = push_router.device_label
    assert label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit "
                 "Version/17.4 Mobile/15E148 Safari/604.1") == "Safari · iOS"
    assert label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36 "
                 "Edg/140.0") == "Edge · Windows"
    assert label("") == "브라우저"
