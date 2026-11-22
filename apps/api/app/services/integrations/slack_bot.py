"""A published agent answering @mentions in Slack, in a thread.

A mention runs a published-bot turn like the web page or API (publisher's account and credits,
strict-local model, the bot's limits). A channel shared with another organisation (Slack
Connect) is an outside audience, answered only when an administrator approved it.

Events are verified by signature (`verify`) and acknowledged within 3 seconds; the turn runs
after (`answer`)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from collections import OrderedDict
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.requests import Request

from app.core.db import SessionLocal
from app.models.chat import ChatSession, RoutingMode, SessionKind
from app.models.integrations import SlackBotChannel, SlackThread, SlackWorkspace
from app.models.publishing import Publication, PublicationStatus
from app.models.user import User, utcnow
from app.services import publishing
from app.services.integrations import accounts
from app.services.integrations.http import ProviderError, request

log = logging.getLogger(__name__)

API = "https://slack.com/api"
#: a request signed longer ago than this is refused (replay)
SKEW_SEC = 300
#: said under the first answer to a person in Slack (the bot's notice)
NOTICE = ("이 답은 이 에이전트를 공개한 사람의 계정으로 처리되고 기록됩니다. "
          "개인정보는 적지 마세요.")
#: event ids already taken, so a retried delivery runs once
_seen: OrderedDict[str, float] = OrderedDict()
_SEEN_MAX = 2000
_TASKS: set[asyncio.Task] = set()
_MENTION = re.compile(r"<@[A-Z0-9]+(?:\|[^>]*)?>")


def verify(secret: str, timestamp: str, body: bytes, signature: str, *,
           now: float | None = None) -> bool:
    """Slack's request signature (v0, HMAC-SHA256 of `v0:ts:body`) within `SKEW_SEC`."""
    if not secret or not timestamp.isdigit() or not signature.startswith("v0="):
        return False
    if abs((now if now is not None else time.time()) - int(timestamp)) > SKEW_SEC:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def first_time(event_id: str) -> bool:
    if not event_id:
        return True
    if event_id in _seen:
        return False
    _seen[event_id] = time.monotonic()
    while len(_seen) > _SEEN_MAX:
        _seen.popitem(last=False)
    return True


async def bot_call(token: str, method: str, **params: Any) -> dict[str, Any]:
    response = await request("POST", f"{API}/{method}", token=token, data=params)
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict) or not body.get("ok"):
        raise ProviderError("slack_" + str((body or {}).get("error") or "error")[:40], 502)
    return body


def allowed(pub: Publication, shared: bool) -> str | None:
    """Why the bot may not answer in this channel now, or None."""
    if pub.status != PublicationStatus.approved.value:
        return "bot_paused"
    if shared and not publishing.opens_outside(pub):
        # outside audience requires administrator approval
        return "outside_not_approved"
    return None


def handle(payload: dict[str, Any]) -> None:
    """Takes one `event_callback`; the answer runs in the background."""
    event = payload.get("event") or {}
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return
    if not first_time(str(payload.get("event_id") or "")):
        return
    try:
        task = asyncio.get_running_loop().create_task(_safe_answer(payload))
    except RuntimeError:
        return
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def drain() -> None:
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


async def _safe_answer(payload: dict[str, Any]) -> None:
    try:
        async with SessionLocal() as db:
            await answer(db, payload)
    except Exception as exc:  # noqa: BLE001 — a Slack event must never take the API down
        log.warning("slack bot answer failed (%s)", type(exc).__name__)


def _synthetic_request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/integrations/slack/events",
                    "headers": [(b"user-agent", b"Slack")], "client": ("slack", 0)})


async def answer(db: AsyncSession, payload: dict[str, Any]) -> str | None:
    """Runs the bot's turn for one mention and replies in its thread; the refusal code when
    it did not."""
    from app.routers import bot_api, publications

    event = payload.get("event") or {}
    team = str(payload.get("team_id") or event.get("team") or "")
    channel = str(event.get("channel") or "")
    link = (await db.exec(select(SlackBotChannel).where(
        SlackBotChannel.team_id == team, SlackBotChannel.channel_id == channel))).first()
    token = await accounts.bot_token(db, team)
    if link is None or not token:
        return "not_linked"
    pub = await db.get(Publication, link.publication_id)
    thread = str(event.get("thread_ts") or event.get("ts") or "")
    if pub is None:
        return "not_linked"
    workspace = await db.get(SlackWorkspace, team)
    # decided again for every mention: a channel shared later, a person from another
    # organisation, or a workspace the administrator never marked as ours is outside
    shared = bool(link.shared or payload.get("is_ext_shared_channel")
                  or (event.get("user_team") and event.get("user_team") != team)
                  or workspace is None or not workspace.internal)
    if reason := allowed(pub, shared):
        await _reply(token, channel, thread, "이 에이전트는 지금 답할 수 없습니다.")
        return reason
    question = _MENTION.sub("", str(event.get("text") or "")).strip()
    if not question:
        return "empty"
    visitor, inserted = await publishing.visitor_row(
        db, pub.id, anon_id=f"slack:{team}:{event.get('user') or ''}"[:64])
    if visitor.blocked:
        return "visitor_blocked"
    first = inserted or not visitor.consented_at
    if first:
        # said before the first answer: whose account handles what they write
        await _reply(token, channel, thread, f"_{NOTICE}_")
    visitor.consent_version, visitor.consented_at = pub.consent_version, visitor.consented_at \
        or utcnow()
    visitor.last_seen_at = utcnow()
    db.add(visitor)
    key = f"{team}:{channel}:{thread}"[:120]
    kept = (await db.exec(select(SlackThread).where(SlackThread.key == key))).first()
    session = await db.get(ChatSession, kept.session_id) if kept else None
    version = await publications._answering(db, pub, None if shared else
                                            await db.get(User, pub.owner_id))
    if session is None:
        session = ChatSession(user_id=pub.owner_id, kind=SessionKind.chat, title="",
                              agent_id=version.agent_snapshot_id, model=version.model,
                              routing_mode=RoutingMode.manual, publication_id=pub.id,
                              visitor_id=visitor.id, publication_version_id=version.id,
                              viewable=pub.conversation_view == "on")
        db.add(session)
        await db.flush()
        db.add(SlackThread(key=key, session_id=session.id))
        await publishing.bump(db, pub.id, publishing.day_window(), "conversations")
    await db.commit()
    owner = await db.get(User, pub.owner_id)
    try:
        response = await publications.run_published_turn(
            db, pub, visitor, session, content=question, retry_of=None,
            request=_synthetic_request(),
            # inside the organisation the members' audience; an outside channel stays anonymous
            user=None if shared else owner, ip_key=None, channel="slack", by_key=True,
            question=question)
    except Exception as exc:  # noqa: BLE001 — said in the thread, not raised
        code = getattr(exc, "detail", None) or type(exc).__name__
        await _reply(token, channel, thread, "지금은 답할 수 없습니다. 잠시 뒤 다시 불러 주세요.")
        return str(code)
    if not hasattr(response, "body_iterator"):
        await _reply(token, channel, thread, "지금은 답할 수 없습니다. 잠시 뒤 다시 불러 주세요.")
        return "refused"
    try:
        final = await bot_api._final(session, bot_api._events(response.body_iterator),
                                     show_sources=pub.show_sources)
    except bot_api.Failed:
        await _reply(token, channel, thread, "답을 쓰지 못했습니다.")
        return "answer_failed"
    text = quiet(final["answer"])
    if final["citations"]:
        text += "\n\n" + "\n".join(
            f"[{c['index']}] " + (f"<{c['url']}|{_label(c['source'])}>" if c.get("url")
                                  else quiet(c["source"]))
            for c in final["citations"])
    await _reply(token, channel, thread, text)
    return None


def quiet(text: str) -> str:
    """Model-written text that cannot ping a channel or a person (`<!here>`, `<@U1>`) nor
    forge a link: Slack's control characters escaped."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _label(text: str) -> str:
    return quiet(text).replace("|", "¦")[:200]


async def _reply(token: str, channel: str, thread: str, text: str) -> None:
    try:
        await bot_call(token, "chat.postMessage", channel=channel, thread_ts=thread,
                       text=text[:39_000])
    except ProviderError as exc:
        log.info("slack reply not posted: %s", exc.code)
