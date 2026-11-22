"""Web push notices: VAPID keys, devices, and delivery from the replica that ran the job.

Payloads carry a short title and, at most, the conversation's title; never what was written.
Every check that must hold across replicas (rate limit, one-time notices) reads the database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core import logs
from app.core.config import settings
from app.models.chat import ChatSession, Message, RoutingMode
from app.models.push import PUSH_EVENTS, PushSent, PushSubscription
from app.models.settings import SystemSetting
from app.models.user import User, UserStatus, utcnow
from app.models.workspace import Artifact
from app.schemas.auth import Preferences
from app.services import settings_store
from app.services import webpush_crypto as wc

log = logging.getLogger(__name__)

EVENTS = PUSH_EVENTS

#: push services the major browsers use; a subscription elsewhere is refused (SSRF)
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)

#: per message; a notice older than a day is no longer news
TTL_SEC = 24 * 3600
ATTEMPTS = 3
_STRICT_ACTIONS = {"strict_local", "route_strict_local", "internal_strict_local"}

#: background deliveries, kept referenced until done
_TASKS: set[asyncio.Task] = set()
_keys: dict[str, str] = {}


# ── keys ───────────────────────────────────────────────────────────────────


async def vapid_keys(db: AsyncSession | None = None) -> tuple[str, str]:
    """(private, public) base64url. Env pins a pair; else the stored one, made on first need.

    Only the private key is stored and the public half derived, so replicas racing on a fresh
    install cannot end up with halves of different pairs.
    """
    if settings.vapid_private_key:
        public = settings.vapid_public_key or wc.public_from_private(settings.vapid_private_key)
        return settings.vapid_private_key, public
    if "private" in _keys:
        return _keys["private"], _keys["public"]
    if db is None:
        async with core_db.SessionLocal() as own:
            return await vapid_keys(own)
    private = await _stored_private(db)
    if not private:
        private, _ = wc.generate_keypair()
        row = await db.get(SystemSetting, settings_store.PUSH_VAPID_PRIVATE)
        if row is None:
            row = SystemSetting(key=settings_store.PUSH_VAPID_PRIVATE, updated_by="system")
        # a row that no longer opens (secret key changed) is replaced; browsers re-subscribe
        # (`lib/push.ts`)
        row.value = settings_store.encrypt_secret(private)
        row.secret = True
        row.updated_at = utcnow()
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            # another replica stored its pair first; use that one
            await db.rollback()
            private = await _stored_private(db)
        if not private:
            raise RuntimeError("web push key unavailable")
    _keys.update(private=private, public=wc.public_from_private(private))
    return _keys["private"], _keys["public"]


async def _stored_private(db: AsyncSession) -> str:
    row = await db.get(SystemSetting, settings_store.PUSH_VAPID_PRIVATE)
    if row is None or not row.value:
        return ""
    return settings_store.decrypt_secret(row.value) if row.secret else row.value


async def subject(db: AsyncSession) -> str:
    if settings.vapid_subject:
        return settings.vapid_subject
    contact = await settings_store.contact_email(db)
    if contact:
        return f"mailto:{contact}"
    base = settings.app_base_url.strip()
    return base if base.startswith("https://") else "mailto:admin@localhost"


# ── devices ────────────────────────────────────────────────────────────────


def endpoint_allowed(endpoint: str) -> bool:
    """A known push service over https, or a host the operator listed (tests, own service)."""
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if not host or parts.username or parts.password or len(endpoint) > 1000:
        return False
    if host in {h.lower() for h in settings.push_extra_hosts}:
        return parts.scheme in ("https", "http")
    if parts.scheme != "https":
        return False
    if any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES):
        return True
    # Chrome also hands out FCM addresses on Google's own hosts (e.g. jmt17.google.com/fcm/send/…)
    return (host == "google.com" or host.endswith(".google.com")) and parts.path.startswith(
        ("/fcm/", "/wp/")
    )


# ── who gets what ──────────────────────────────────────────────────────────


def webpush_key_check(p256dh: str, auth: str) -> None:
    """ValueError unless the browser's keys are a P-256 point and a 16-byte secret."""
    try:
        point = wc.b64u_decode(p256dh)
        secret = wc.b64u_decode(auth)
        wc.public_key_from_raw(point)
    except Exception as exc:  # noqa: BLE001 — any decode failure is a bad key
        raise ValueError("bad keys") from exc
    if len(point) != 65 or len(secret) != 16:
        raise ValueError("bad keys")


def wants(user: User, event: str) -> bool:
    if event == "test":
        return True
    try:
        chosen = Preferences.of(user).push_events
    except ValueError:
        chosen = {}
    return chosen.get(event, EVENTS.get(event, False))


def _english(user: User) -> bool:
    try:
        return Preferences.of(user).ui_language == "en"
    except ValueError:
        return False


def _internal_titles(user: User) -> bool:
    try:
        return Preferences.of(user).push_internal_titles
    except ValueError:
        return False


async def session_is_internal(db: AsyncSession, session: ChatSession) -> bool:
    """Strict-local or internal material anywhere in the conversation."""
    if session.project_id or session.routing_mode == RoutingMode.super_strict:
        return True
    if (session.model or "").startswith("strict-local/"):
        return True
    rows = (
        await db.exec(
            select(Message.routing, Message.attachments, Message.model).where(
                Message.session_id == session.id
            )
        )
    ).all()
    for routing, attachments, model in rows:
        if attachments or (model or "").startswith("strict-local/"):
            return True
        if isinstance(routing, dict) and (
            {routing.get("action"), routing.get("initialAction")} & _STRICT_ACTIONS
        ):
            return True
    return False


# ── wording ────────────────────────────────────────────────────────────────

#: job kind → (finished, failed)
JOB_TITLES: dict[str, tuple[str, str]] = {
    "report": ("보고서가 완성됐습니다", "보고서를 만들지 못했습니다"),
    "deck": ("슬라이드가 완성됐습니다", "슬라이드를 만들지 못했습니다"),
    "page": ("웹 페이지가 완성됐습니다", "웹 페이지를 만들지 못했습니다"),
    "office": ("문서가 완성됐습니다", "문서를 만들지 못했습니다"),
    "research": ("심층 조사가 끝났습니다", "심층 조사를 끝내지 못했습니다"),
    "media": ("영상·음성 읽기가 끝났습니다", "영상·음성을 읽지 못했습니다"),
    "video": ("영상이 완성됐습니다", "영상을 만들지 못했습니다"),
    "studio": ("스튜디오 결과물이 완성됐습니다", "스튜디오 결과물을 만들지 못했습니다"),
    "chain": ("에이전트 체인이 끝났습니다", "에이전트 체인이 멈췄습니다"),
}
GENERIC_BODY = "KloudChat에서 확인하세요"

#: the notices' fixed wording in English, for people whose screen is in English
EN_WORDS: dict[str, str] = {
    "보고서가 완성됐습니다": "Your report is ready",
    "보고서를 만들지 못했습니다": "The report could not be made",
    "슬라이드가 완성됐습니다": "Your slides are ready",
    "슬라이드를 만들지 못했습니다": "The slides could not be made",
    "웹 페이지가 완성됐습니다": "Your web page is ready",
    "웹 페이지를 만들지 못했습니다": "The web page could not be made",
    "문서가 완성됐습니다": "Your document is ready",
    "문서를 만들지 못했습니다": "The document could not be made",
    "심층 조사가 끝났습니다": "Your deep research is done",
    "심층 조사를 끝내지 못했습니다": "The deep research could not be finished",
    "영상·음성 읽기가 끝났습니다": "Reading your video or audio is done",
    "영상·음성을 읽지 못했습니다": "The video or audio could not be read",
    "영상이 완성됐습니다": "Your video is ready",
    "영상을 만들지 못했습니다": "The video could not be made",
    "스튜디오 결과물이 완성됐습니다": "Your studio result is ready",
    "스튜디오 결과물을 만들지 못했습니다": "The studio result could not be made",
    "에이전트 체인이 끝났습니다": "Your agent chain is done",
    "에이전트 체인이 멈췄습니다": "Your agent chain stopped",
    GENERIC_BODY: "Check it in KloudChat",
    "알림이 켜졌습니다": "Notifications are on",
    "이 기기로 알림을 받습니다": "This device will receive notifications",
    "이번 달 크레딧이 90%를 넘었습니다": "You have used over 90% of this month's credits",
    "문의에 답변이 달렸습니다": "Your inquiry has a reply",
    "문의 처리 상태가 바뀌었습니다": "The status of your inquiry changed",
    "문의·신고": "Inquiries and reports",
    "공유 에이전트 승인 요청이 왔습니다": "A shared agent is waiting for approval",
    "공유 에이전트가 승인됐습니다": "Your shared agent was approved",
    "공유 에이전트 요청이 반려됐습니다": "Your shared agent request was declined",
    "에이전트 공개 승인 요청이 왔습니다": "An agent publication is waiting for approval",
    "에이전트 공개가 승인됐습니다": "Your agent publication was approved",
    "에이전트 공개 요청이 반려됐습니다": "Your agent publication request was declined",
    "방문자가 대화를 보냈습니다": "A visitor sent you a conversation",
}


@dataclass(frozen=True)
class Notice:
    event: str
    title: str
    #: the conversation's (or document's) title; dropped for internal material unless allowed
    label: str = ""
    url: str = "/"
    tag: str = ""
    internal: bool = False
    #: sent at most once per user per key (e.g. one credit warning per cycle)
    once_key: str | None = None

    def payload(self, user: User) -> dict[str, Any]:
        label = self.label.strip()
        if self.internal and not _internal_titles(user):
            label = ""
        body = label[:80] if label else GENERIC_BODY
        title = self.title
        if _english(user):
            title, body = EN_WORDS.get(title, title), EN_WORDS.get(body, body)
        url = self.url if self.url.startswith("/") and not self.url.startswith("//") else "/"
        return {"title": title, "body": body, "url": url, "tag": self.tag or self.event}


# ── sending ────────────────────────────────────────────────────────────────


def notify(user_id: str | None, notice: Notice) -> None:
    """Fire and forget from the replica that finished the work; never raises.

    The one fan-out point for notices: other channels hook into `_safe_deliver` with the same
    `Notice`, not into the call sites.
    """
    if not user_id or (notice.event not in EVENTS and notice.event != "test"):
        return
    try:
        task = asyncio.get_running_loop().create_task(_safe_deliver(user_id, notice))
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _safe_deliver(user_id: str, notice: Notice) -> None:
    """Web push and Slack side by side: a slow or failing one never holds back the other."""
    jobs = [_push_only(user_id, notice)]
    if notice.event != "test":
        # Slack, for people who turned it on; opt-in, `internal` honoured, never raises
        from app.services.integrations import slack_notify

        jobs.append(slack_notify.deliver(user_id, notice.title, notice.label, notice.url,
                                         notice.internal))
    await asyncio.gather(*jobs, return_exceptions=True)


async def _push_only(user_id: str, notice: Notice) -> None:
    try:
        await deliver(user_id, notice)
    except Exception as exc:  # noqa: BLE001 — a notice must never disturb the job
        log.warning("push notice not sent: %s", logs.safe(str(exc)))


async def drain() -> None:
    """Tests and shutdown: wait for notices in flight."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


#: artifact kind → job wording
_ARTIFACT_JOBS = {
    "report": "report",
    "deck": "deck",
    "html": "page",
    "hwp": "office",
    "docx": "office",
    "pptx": "office",
    "video": "video",
}


async def notify_job(
    db: AsyncSession,
    session: ChatSession | None,
    kind: str,
    *,
    ok: bool,
    artifact_id: str | None = None,
    label: str = "",
) -> None:
    """Long job finished or failed in a conversation (not a published bot's visitor turn).

    With `artifact_id`, the artifact's own kind words the notice (HWP report: 「문서」).
    """
    if session is None or getattr(session, "publication_id", None):
        return
    if artifact_id:
        made = await db.get(Artifact, artifact_id)
        if made is not None:
            kind = _ARTIFACT_JOBS.get(str(made.kind), kind)
    titles = JOB_TITLES.get(kind) or JOB_TITLES["report"]
    url = f"/s/{session.id}" + (f"?artifact={artifact_id}" if artifact_id else "")
    try:
        internal = await session_is_internal(db, session)
    except Exception:  # noqa: BLE001 — unsure means private
        internal = True
    notify(
        session.user_id,
        Notice(
            event="job_done" if ok else "job_failed",
            title=titles[0] if ok else titles[1],
            label=label or session.title,
            url=url,
            tag=f"job:{session.id}",
            internal=internal,
        ),
    )


async def notify_session_job(
    session_id: str | None, kind: str, *, ok: bool, artifact_id: str | None = None
) -> None:
    """`notify_job` from code holding only ids; opens its own session, never raises."""
    if not session_id:
        return
    try:
        async with core_db.SessionLocal() as db:
            session = await db.get(ChatSession, session_id)
            await notify_job(db, session, kind, ok=ok, artifact_id=artifact_id)
    except Exception as exc:  # noqa: BLE001 — a notice must never disturb the job
        log.warning("push notice not queued: %s", logs.safe(str(exc)))


async def notify_admins(db: AsyncSession, notice: Notice, *, but: str | None = None) -> None:
    """Every active admin (except `but`, the one who acted)."""
    from app.models.user import UserRole

    ids = (
        await db.exec(
            select(User.id).where(User.role == UserRole.admin, User.status == UserStatus.active)
        )
    ).all()
    for user_id in ids:
        if user_id != but:
            notify(user_id, notice)


def notify_file_read(stored: Any, *, ok: bool) -> None:
    """An uploaded video or audio file was read (or not)."""
    if stored.session_id:
        url = f"/s/{stored.session_id}"
    elif stored.project_id:
        url = f"/projects/{stored.project_id}"
    else:
        url = f"/files?open={stored.id}"
    titles = JOB_TITLES["media"]
    notify(
        stored.user_id,
        Notice(
            event="job_done" if ok else "job_failed",
            title=titles[0] if ok else titles[1],
            label=stored.name or "",
            url=url,
            tag=f"file:{stored.id}",
            internal=True,
        ),
    )


async def deliver(user_id: str, notice: Notice) -> int:
    """Send to every device of the user; the number of devices that took it."""
    async with core_db.SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None or user.status != UserStatus.active or not wants(user, notice.event):
            return 0
        subs = (
            await db.exec(select(PushSubscription).where(PushSubscription.user_id == user_id))
        ).all()
        if not subs:
            return 0
        if not await _admit(db, user_id, notice):
            return 0
        body = json.dumps(notice.payload(user), ensure_ascii=False).encode()
        private, public = await vapid_keys(db)
        sub_claim = await subject(db)
        sent = 0
        async with _client() as client:
            for sub in subs:
                outcome = await _send_one(client, sub, body, private, public, sub_claim, notice)
                if outcome == "gone":
                    await db.exec(delete(PushSubscription).where(PushSubscription.id == sub.id))
                elif outcome == "ok":
                    sub.last_sent_at = utcnow()
                    db.add(sub)
                    sent += 1
        await db.commit()
        return sent


async def _admit(db: AsyncSession, user_id: str, notice: Notice) -> bool:
    """Per-user rate limit and one-time keys, on the shared log; records the notice."""
    now = utcnow()
    recent = (
        await db.exec(
            select(func.count())
            .select_from(PushSent)
            .where(PushSent.user_id == user_id, col(PushSent.at) > now - timedelta(minutes=1))
        )
    ).one()
    if recent >= max(1, settings.push_per_minute):
        log.info("push notice dropped: rate limit")
        return False
    db.add(PushSent(user_id=user_id, event=notice.event, once_key=notice.once_key, at=now))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    await db.exec(
        delete(PushSent).where(
            PushSent.user_id == user_id,
            col(PushSent.once_key).is_(None),
            col(PushSent.at) < now - timedelta(days=1),
        )
    )
    await db.exec(
        delete(PushSent).where(
            PushSent.user_id == user_id, col(PushSent.at) < now - timedelta(days=62)
        )
    )
    return True


async def _send_one(
    client: httpx.AsyncClient,
    sub: PushSubscription,
    body: bytes,
    private: str,
    public: str,
    sub_claim: str,
    notice: Notice,
) -> str:
    """"ok", "gone" (delete the device) or "failed"."""
    if not endpoint_allowed(sub.endpoint):
        return "gone"
    try:
        message = wc.encrypt(body, sub.p256dh, sub.auth)
    except ValueError:
        return "gone"
    headers = {
        "TTL": str(TTL_SEC),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Urgency": "normal",
        # same-tag notices replace each other at the push service too (≤ 32 url-safe chars)
        "Topic": _topic(notice.tag or notice.event),
    }
    for attempt in range(ATTEMPTS):
        headers["Authorization"] = wc.vapid_header(sub.endpoint, private, public, sub_claim)
        try:
            resp = await client.post(sub.endpoint, content=message, headers=headers)
        except httpx.HTTPError:
            resp = None
        if resp is not None:
            if resp.status_code in (200, 201, 202):
                return "ok"
            if resp.status_code in (404, 410):
                return "gone"
            if resp.status_code not in (429,) and resp.status_code < 500:
                log.info("push refused by service: %s", resp.status_code)
                return "failed"
        if attempt + 1 < ATTEMPTS:
            await asyncio.sleep(_backoff(attempt, resp))
    return "failed"


def _client() -> httpx.AsyncClient:
    """The delivery client; tests swap in a mock transport."""
    return httpx.AsyncClient(timeout=10.0, follow_redirects=False)


def _backoff(attempt: int, resp: httpx.Response | None) -> float:
    retry_after = resp.headers.get("Retry-After", "") if resp is not None else ""
    if retry_after.isdigit():
        return min(float(retry_after), 30.0)
    return min(2.0**attempt * settings.push_backoff_sec + random.uniform(0, 0.25), 30.0)


def _topic(tag: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in tag)
    return safe[:32] or "notice"


__all__ = [
    "EVENTS",
    "JOB_TITLES",
    "Notice",
    "deliver",
    "drain",
    "endpoint_allowed",
    "notify",
    "notify_job",
    "notify_admins",
    "notify_file_read",
    "notify_session_job",
    "session_is_internal",
    "vapid_keys",
    "wants",
    "webpush_key_check",
]
