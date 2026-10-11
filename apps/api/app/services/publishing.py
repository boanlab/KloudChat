"""Published agents (공개 챗봇): versions, limits, the visitor's turn, and what the owner sees.

A bot answers on its owner's account: a visitor's conversation is a `sessions` row owned by
the publisher and marked with the publication, and the ordinary turn runs it. The turn is
constrained here, on the session rather than on the caller:

- the model is a strict-local one, fixed with the version (refused when none exists);
- the agent is the version's hidden copy (`Agent.snapshot_of`) with its knowledge text;
- tools are 자료 찾기 and 계산 only, and no memory, 개인 맞춤 설정, project, connector, skill
  or other file of the owner's reaches the turn;
- limits per visitor, per address (visitors without an account), per bot and per day, the
  owner's daily credit cap, a concurrency cap per bot and across the instance, and a short
  queue before 429.

A visitor without an account is a signed browser cookie (`VISITOR_COOKIE`), nothing more:
they reach a bot only once an administrator opened it outside, and only their own
conversations.

The owner sees counts, frequent topics, unanswered questions (personal details masked) and
👍👎 — not conversations, except one a visitor sent them or, with 「대화 열람」 on, those
started after the visitor's consent, both masked when read.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import random
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core import logs
from app.core.config import settings
from app.models.chat import ChatSession, Message, PipelineRun, Role
from app.models.publishing import (
    Publication,
    PublicationCounter,
    PublicationQuestion,
    PublicationSlot,
    PublicationStatus,
    PublicationVersion,
    PublicationVisibility,
    PublicationVisitor,
)
from app.models.user import AuditEvent, CreditLedger, User, UserStatus, utcnow
from app.models.workspace import Agent, StoredFile, Visibility
from app.services import governance, skill_specs

log = logging.getLogger(__name__)

KST = ZoneInfo(settings.timezone)

#: The limits a bot runs under unless its owner lowers them (`Publication.limits`). The
#: per-address ones count visitors without an account only: members answer for their own.
DEFAULT_LIMITS: dict[str, int] = {
    "perVisitorPerMinute": 10,
    "perVisitorPerDay": 200,
    "perBotPerMinute": 60,
    "perIpPerMinute": 20,
    "perIpPerDay": 400,
    "turnsPerDay": 2000,
    "tokensPerDay": 4_000_000,
    "maxInputChars": 8000,
    "turnsPerConversation": 100,
    "concurrency": 4,
    "perKeyPerMinute": 30,
    "perKeyPerDay": 2000,
}
#: The owner's own cap on what the bot may spend a day, in credits (`limits.creditsPerDay`).
#: Required before the bot is opened outside; no default, since only the owner knows it.
CREDITS_PER_DAY = "creditsPerDay"
CREDITS_PER_DAY_MAX = 100_000_000
#: Visitors without an account, per address and day: new visitors (cookies) and conversations.
#: Not the owner's to change; they bound what clearing cookies can buy.
ANON_VISITORS_PER_IP_DAY = 30
ANON_CONVERSATIONS_PER_IP_DAY = 100
#: How long a turn waits for a free slot before 429.
QUEUE_WAIT_SEC = 15.0
#: A slot older than this is a crashed turn's leftover.
SLOT_TTL_SEC = 600
#: Tools a published bot may call: its own material and the calculator. Nothing that leaves
#: the boundary, writes into the owner's account (artifacts, notes) or runs code.
PUBLISHABLE_TOOLS = frozenset({"search_knowledge", "calculate"})
#: Minute counters older than this are swept; day counters are kept for the statistics.
_MINUTE_KEEP = timedelta(days=2)
_DAY_KEEP = timedelta(days=400)
#: Up to this many unanswered questions are shown; older ones are swept with retention.
UNANSWERED_SHOWN = 50
#: A reason to read a conversation as an administrator is required and recorded.
ADMIN_REASON_MIN = 4


class Refused(Exception):
    """A request a bot turns away: `code` for the client, HTTP `status`, `retry_after`."""

    def __init__(self, code: str, status: int = 409, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after


# ── limits ─────────────────────────────────────────────────────────────


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def limits_of(pub: Publication) -> dict[str, int]:
    """The bot's limits: the defaults, lowered where the owner asked (never raised), and the
    owner's daily credit cap when there is one."""
    out = dict(DEFAULT_LIMITS)
    for key, value in (pub.limits or {}).items():
        if key in out and _whole(value) and value >= 1:
            out[key] = min(value, DEFAULT_LIMITS[key])
    if (cap := credits_cap(pub)) is not None:
        out[CREDITS_PER_DAY] = cap
    return out


def credits_cap(pub: Publication) -> int | None:
    value = (pub.limits or {}).get(CREDITS_PER_DAY)
    return min(value, CREDITS_PER_DAY_MAX) if _whole(value) and value >= 1 else None


def clean_limits(raw: Any) -> dict[str, int]:
    """What an owner may store: known keys, whole numbers from 1 up to the default; the daily
    credit cap from 1 up to `CREDITS_PER_DAY_MAX`."""
    out: dict[str, int] = {}
    for key, value in (raw or {}).items() if isinstance(raw, dict) else []:
        if key in DEFAULT_LIMITS and _whole(value):
            out[key] = max(1, min(value, DEFAULT_LIMITS[key]))
        elif key == CREDITS_PER_DAY and _whole(value):
            out[key] = max(1, min(value, CREDITS_PER_DAY_MAX))
    return out


def minute_window(now: datetime | None = None) -> str:
    return "m" + (now or utcnow()).astimezone(UTC).strftime("%Y%m%d%H%M")


def day_window(now: datetime | None = None) -> str:
    """The local calendar day: a daily limit resets at local midnight."""
    return "d" + (now or utcnow()).astimezone(KST).strftime("%Y%m%d")


def _seconds_to_next_minute(now: datetime) -> int:
    return max(1, 60 - now.second)


def _seconds_to_local_midnight(now: datetime) -> int:
    local = now.astimezone(KST)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - local).total_seconds()))


async def bump(db: AsyncSession, publication_id: str, window: str, key: str,
               by: int = 1) -> None:
    """Adds `by` to one counter with a single upsert. Caller commits."""
    if not by:
        return
    table = PublicationCounter.__table__
    conn = await db.connection()
    insert = pg_insert if conn.dialect.name == "postgresql" else sqlite_insert
    statement = insert(table).values(publication_id=publication_id, window=window,
                                     key=key[:120], value=by)
    statement = statement.on_conflict_do_update(
        index_elements=["publication_id", "window", "key"],
        set_={"value": table.c.value + statement.excluded.value},
    )
    await db.exec(statement)


async def counted(db: AsyncSession, publication_id: str, window: str, key: str) -> int:
    row = (await db.exec(select(PublicationCounter.value).where(
        PublicationCounter.publication_id == publication_id,
        PublicationCounter.window == window, PublicationCounter.key == key))).first()
    return int(row or 0)


def local_midnight(now: datetime | None = None) -> datetime:
    """The start of the local day `day_window` counts, as an instant in UTC (as stored)."""
    local = (now or utcnow()).astimezone(KST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def credits_today(db: AsyncSession, publication_id: str,
                        now: datetime | None = None) -> int:
    """Credits the bot's turns charged its owner since local midnight."""
    spent = (await db.exec(select(func.coalesce(func.sum(CreditLedger.delta), 0)).where(
        CreditLedger.publication_id == publication_id, CreditLedger.delta < 0,
        col(CreditLedger.created_at) >= local_midnight(now)))).one()
    return -int(spent or 0)


async def admit(db: AsyncSession, pub: Publication, visitor_id: str, *,
                ip_key: str | None = None, channel: str = "web", by_key: bool = False,
                now: datetime | None = None) -> None:
    """Raises `Refused` (429) when the visitor, their address (a visitor without an account,
    `ip_key`), the bot or the day is over its limit; else counts the turn, and the channel it
    came through. A key's caller (`by_key`) is a program: it has the key's limits instead of
    a person's. Caller commits."""
    now = now or utcnow()
    limits = limits_of(pub)
    minute, day = minute_window(now), day_window(now)
    next_minute, midnight = _seconds_to_next_minute(now), _seconds_to_local_midnight(now)
    per_minute = limits["perKeyPerMinute" if by_key else "perVisitorPerMinute"]
    per_day = limits["perKeyPerDay" if by_key else "perVisitorPerDay"]
    if await counted(db, pub.id, minute, f"turns:v:{visitor_id}") >= per_minute:
        raise Refused("visitor_rate_limited", 429, next_minute)
    if await counted(db, pub.id, day, f"turns:v:{visitor_id}") >= per_day:
        raise Refused("visitor_daily_limit", 429, midnight)
    if ip_key:
        if await counted(db, pub.id, minute, f"turns:ip:{ip_key}") >= limits["perIpPerMinute"]:
            raise Refused("visitor_rate_limited", 429, next_minute)
        if await counted(db, pub.id, day, f"turns:ip:{ip_key}") >= limits["perIpPerDay"]:
            raise Refused("visitor_daily_limit", 429, midnight)
    if await counted(db, pub.id, minute, "turns") >= limits["perBotPerMinute"]:
        raise Refused("bot_rate_limited", 429, next_minute)
    if await counted(db, pub.id, day, "turns") >= limits["turnsPerDay"]:
        raise Refused("daily_turns_reached", 429, midnight)
    if await counted(db, pub.id, day, "tokens") >= limits["tokensPerDay"]:
        raise Refused("daily_tokens_reached", 429, midnight)
    if (cap := credits_cap(pub)) is not None and await credits_today(db, pub.id, now) >= cap:
        raise Refused("daily_credits_reached", 429, midnight)
    await bump(db, pub.id, minute, f"turns:v:{visitor_id}")
    await bump(db, pub.id, day, f"turns:v:{visitor_id}")
    if ip_key:
        await bump(db, pub.id, minute, f"turns:ip:{ip_key}")
        await bump(db, pub.id, day, f"turns:ip:{ip_key}")
    await bump(db, pub.id, minute, "turns")
    await bump(db, pub.id, day, "turns")
    await bump(db, pub.id, day, f"channel:{channel if channel in CHANNEL_NAMES else 'web'}")


async def admit_anonymous(db: AsyncSession, pub: Publication, ip_key: str, what: str,
                          per_day: int, now: datetime | None = None) -> None:
    """Counts a new visitor or conversation from an address without an account, refusing it
    (429) past `per_day`. Caller commits."""
    now = now or utcnow()
    day = day_window(now)
    key = f"{what}:ip:{ip_key}"
    if await counted(db, pub.id, day, key) >= per_day:
        raise Refused("visitor_daily_limit", 429, _seconds_to_local_midnight(now))
    await bump(db, pub.id, day, key)


# ── visitors without an account ────────────────────────────────────────


#: The browser cookie that names a visitor without an account: `<id>.<signature>`.
VISITOR_COOKIE = "kchat_bot_visitor"
#: The same, in a frame on another site: `SameSite=None; Secure; Partitioned`.
EMBED_VISITOR_COOKIE = "kchat_bot_visitor_embed"
VISITOR_COOKIE_DAYS = 365
_ANON_ID = re.compile(r"[A-Za-z0-9_-]{22,43}")


def _visitor_key() -> bytes:
    secret = settings.secret_key or settings.jwt_secret
    return hashlib.sha256(b"kloudchat-bot-visitor\0" + secret.encode()).digest()


def _sign(anon_id: str) -> str:
    mac = hmac.new(_visitor_key(), anon_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def new_visitor_cookie() -> tuple[str, str]:
    """A fresh anonymous id and the cookie value that carries it."""
    anon_id = secrets.token_urlsafe(18)
    return anon_id, f"{anon_id}.{_sign(anon_id)}"


def visitor_from_cookie(value: str | None) -> str | None:
    """The anonymous id a cookie carries, or None when it is missing or not ours."""
    anon_id, _, signature = (value or "").partition(".")
    if not _ANON_ID.fullmatch(anon_id) or not signature:
        return None
    return anon_id if hmac.compare_digest(signature, _sign(anon_id)) else None


def ip_key(ip: str) -> str:
    """An address as the counters key it: keyed, so the stored key does not give it back."""
    return hmac.new(_visitor_key(), (ip or "-").encode(), hashlib.sha256).hexdigest()[:20]


async def acquire_slot(publication_id: str, *, per_bot: int, instance_max: int,
                       wait: float = QUEUE_WAIT_SEC) -> str | None:
    """A running-turn slot, waiting in line up to `wait` seconds; None when none came free.

    Rows in `publication_slots` are the turns running across replicas: a new row's place
    among the live ones decides whether it may run; one that may not withdraws and tries
    again after a short, jittered pause."""
    deadline = time.monotonic() + wait
    while True:
        async with core_db.SessionLocal() as db:
            stale = utcnow() - timedelta(seconds=SLOT_TTL_SEC)
            await db.exec(delete(PublicationSlot).where(col(PublicationSlot.acquired_at) < stale))
            slot = PublicationSlot(publication_id=publication_id)
            db.add(slot)
            await db.commit()
            earlier = or_(
                col(PublicationSlot.acquired_at) < slot.acquired_at,
                and_(PublicationSlot.acquired_at == slot.acquired_at,
                     col(PublicationSlot.id) < slot.id),
            )
            ahead_bot = int((await db.exec(select(func.count()).select_from(PublicationSlot).where(
                PublicationSlot.publication_id == publication_id, earlier))).one())
            ahead_all = int((await db.exec(select(func.count()).select_from(PublicationSlot)
                                           .where(earlier))).one())
            if ahead_bot < per_bot and ahead_all < instance_max:
                return slot.id
            await db.exec(delete(PublicationSlot).where(PublicationSlot.id == slot.id))
            await db.commit()
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.4 + random.random() * 0.6)  # noqa: S311 — jitter, not secrets


async def release_slot(slot_id: str | None) -> None:
    if not slot_id:
        return
    try:
        async with core_db.SessionLocal() as db:
            await db.exec(delete(PublicationSlot).where(PublicationSlot.id == slot_id))
            await db.commit()
    except Exception as exc:  # noqa: BLE001 — a stuck slot expires with the TTL
        log.warning("publication slot %s not released: %s", logs.safe(slot_id), logs.safe(exc))


# ── the visitor's turn ─────────────────────────────────────────────────


@dataclass(slots=True)
class PublishedTurn:
    """What `sessions.send_message` needs to run a visitor's turn on the publisher's account."""

    publication_id: str
    version_id: str
    visitor_id: str
    model_id: str
    #: The question as typed, for the owner's statistics (masked before it is stored).
    question: str = ""
    slot_id: str | None = None
    first_turn: bool = False
    released: bool = field(default=False)

    async def release(self) -> None:
        if not self.released:
            self.released = True
            await release_slot(self.slot_id)


async def holding(events: AsyncIterator[str], turn: PublishedTurn) -> AsyncIterator[str]:
    """Relays a turn's stream and gives its slot back when the turn ends, however it ends.
    Sits inside the detached turn, so a visitor closing the tab does not free the slot early."""
    try:
        async for event in events:
            yield event
    finally:
        await turn.release()


def strict_chat_models(catalogue: list[dict], owner: User) -> list[dict]:
    allowed = set(owner.allowed_models or [])
    return [
        m for m in catalogue
        if m.get("dataBoundary") == "self_hosted" and m.get("strictLocal") is True
        and "chat" in (m.get("kinds") or [])
        # A privacy-only entry is the strict-local route privacy decisions move turns to:
        # kept out of the picker, and exactly what a bot answers with.
        and (not allowed or m.get("id") in allowed)
    ]


def pick_model(agent: Agent, catalogue: list[dict], owner: User, safe_ids: list[str],
               *, needs_tools: bool) -> dict | None:
    """The strict-local model a version answers with: the agent's own model or its
    `strict-local/` twin, then the default model's twin, then the first privacy-safe model.
    None when this instance has no internal model the owner may use."""
    usable = [m for m in strict_chat_models(catalogue, owner)
              if not needs_tools or m.get("supportsTools")]
    by_id = {m["id"]: m for m in usable}

    def strict(model_id: str) -> dict | None:
        if not model_id:
            return None
        if model_id in by_id:
            return by_id[model_id]
        if model_id.startswith("local/"):
            return by_id.get("strict-local/" + model_id.split("/", 1)[1])
        return None

    for candidate in (agent.model, settings.default_chat_model):
        if found := strict(candidate or ""):
            return found
    for model_id in safe_ids or []:
        if model_id in by_id:
            return by_id[model_id]
    return usable[0] if usable else None


# ── preflight, versions ────────────────────────────────────────────────


#: Codes the publish dialog names (`publishing.*` in the web client).
_DISABLED_ALWAYS = ("memory", "personal", "files", "connectors", "web", "documents")


def _tools_of(agent: Agent) -> list[str]:
    """The agent's tools a bot keeps: inherit (null) keeps both, a list keeps what it names."""
    if agent.tools is None:
        return sorted(PUBLISHABLE_TOOLS)
    return sorted(t for t in agent.tools if t in PUBLISHABLE_TOOLS)


def _post_checks(agent: Agent) -> list[str]:
    """The shipped `post` checks a bot keeps; a question card before writing and a person's
    own check skills (which can change after publishing) are left out."""
    out = []
    for key in agent.check_skills or []:
        if str(key).startswith(skill_specs.USER):
            continue
        spec = skill_specs.resolve(str(key), None)
        if spec is not None and spec.stage == "post":
            out.append(str(key))
    return out


async def knowledge_rows(db: AsyncSession, agent: Agent) -> list[StoredFile]:
    return list((await db.exec(select(StoredFile).where(
        StoredFile.user_id == agent.owner_id, StoredFile.agent_id == agent.id,
        col(StoredFile.text) != "").order_by(col(StoredFile.created_at)))).all())


async def preflight(db: AsyncSession, owner: User, agent: Agent, catalogue: list[dict],
                    safe_ids: list[str]) -> dict[str, Any]:
    """Whether `agent` can be published as it stands, the model it would answer with, what a
    bot leaves out, and personal details found in its knowledge (a warning, not a refusal)."""
    problems: list[str] = []
    if agent.owner_id != owner.id or agent.snapshot_of:
        problems.append("not_owner")
    if agent.retired:
        problems.append("retired")
    if agent.sealed:
        problems.append("sealed")
    if agent.kinds and "chat" not in agent.kinds or (agent.surface or "chat") != "chat":
        problems.append("not_chat")
    if agent.pipeline_id:
        problems.append("own_process")
    if owner.status is not UserStatus.active:
        problems.append("account")
    if owner.credits_remaining <= 0:
        problems.append("credits")
    files = await knowledge_rows(db, agent)
    tools = _tools_of(agent)
    model = pick_model(agent, catalogue, owner, safe_ids,
                       needs_tools=bool(files and "search_knowledge" in tools))
    if model is None:
        problems.append("no_local_model")

    disabled = list(_DISABLED_ALWAYS)
    named = set(agent.tools or [])
    if agent.tools is not None and named - PUBLISHABLE_TOOLS:
        disabled.append("tools")
    if agent.skill_ids:
        disabled.append("skills")
    if any(not str(k).startswith(skill_specs.USER)
           and (s := skill_specs.resolve(str(k), None)) is not None and s.stage == "pre"
           for k in agent.check_skills or []):
        disabled.append("questions")
    if any(str(k).startswith(skill_specs.USER) for k in agent.check_skills or []):
        disabled.append("own_checks")

    knowledge = []
    for row in files:
        found = governance.findings({"knowledge": row.text}, scope="egress")
        knowledge.append({
            "name": row.name, "chars": len(row.text),
            "findings": [{"category": f.category, "count": f.count} for f in found],
        })
    return {
        "ok": not problems,
        "problems": problems,
        "model": {"id": model["id"], "label": model.get("label") or model["id"]}
        if model else None,
        "tools": tools,
        "checks": _post_checks(agent),
        "disabled": disabled,
        "knowledge": knowledge,
        "creditsRemaining": owner.credits_remaining,
    }


async def make_version(db: AsyncSession, owner: User, pub: Publication, agent: Agent,
                       model_id: str, note: str = "", *,
                       activate: bool = True) -> PublicationVersion:
    """Fixes the agent as it stands into a new version: a hidden copy of the agent and the
    text of its knowledge. It becomes the active one, or with `activate` false (a bot open
    outside) the one waiting for review. Caller commits."""
    from app.routers.sessions import _agent_catalog_key

    number = int((await db.exec(select(func.max(PublicationVersion.number)).where(
        PublicationVersion.publication_id == pub.id))).one() or 0) + 1
    key = await _agent_catalog_key(db, agent)
    snap = Agent(
        owner_id=owner.id,
        name=agent.name,
        slug=f"bot-{pub.id[:10]}-v{number}-{secrets.token_hex(3)}",
        description=agent.description,
        model=model_id,
        system_prompt=agent.system_prompt,
        tools=_tools_of(agent),
        # Skills deny: none of the owner's skills (which can change) reach a visitor.
        skill_ids=[],
        kinds=["chat"],
        surface="chat",
        base_key=key,
        check_skills=_post_checks(agent),
        check_options=dict(agent.check_options) if agent.check_options else None,
        bundles=list(agent.bundles) if agent.bundles else None,
        process_settings=dict(agent.process_settings) if agent.process_settings else None,
        contact=agent.contact or "",
        guide=agent.guide,
        starters=list(agent.starters or []),
        temperature=agent.temperature,
        color=agent.color,
        enabled=True,
        visibility=Visibility.private,
        snapshot_of=agent.id,
    )
    db.add(snap)
    await db.flush()
    knowledge = []
    for row in await knowledge_rows(db, agent):
        # The text only: the bot reads it, nobody downloads it, and the original's blob may go.
        db.add(StoredFile(user_id=owner.id, agent_id=snap.id, name=row.name, size=row.size,
                          mime=row.mime, storage_key="", text=row.text, tokens=row.tokens,
                          source_url=row.source_url))
        knowledge.append({"name": row.name, "chars": len(row.text)})
    version = PublicationVersion(
        publication_id=pub.id, number=number, agent_snapshot_id=snap.id, model=model_id,
        note=note[:300], created_by=owner.id,
        summary={"agentName": agent.name, "knowledge": knowledge, "tools": snap.tools or [],
                 "checks": snap.check_skills or [], "contact": bool(agent.contact)},
    )
    db.add(version)
    if activate:
        pub.active_version_id = version.id
    else:
        pub.pending_version_id = version.id
    pub.updated_at = utcnow()
    db.add(pub)
    return version


# ── channels ───────────────────────────────────────────────────────────


#: Where a turn came from, for the statistics: the bot's own page and the channels.
CHANNEL_NAMES = ("web", "embed", "api", "mcp")
#: Channels an administrator opens. `embed` needs the bot listed for everyone (`external`);
#: `api` and `mcp` need it open outside at all.
CHANNELS = ("embed", "api", "mcp")
EMBED_ORIGINS_MAX = 10
#: `https://host[:port]`, or `https://*.host` for one level of subdomains. Lower case only.
_ORIGIN = re.compile(r"https://(\*\.)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                     r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?::\d{1,5})?")


def clean_origin(raw: str) -> str | None:
    """An origin as a frame-ancestors source, or None when it is not one this accepts."""
    value = (raw or "").strip().lower().rstrip("/")
    if len(value) > 200 or not _ORIGIN.fullmatch(value):
        return None
    host = value.split("://", 1)[1].split(":", 1)[0]
    # A wildcard covers a site's subdomains, never a whole top-level domain.
    if host.startswith("*.") and host.count(".") < 2:
        return None
    return value


def clean_origins(raw: Any) -> list[str]:
    """The sites an owner listed, cleaned; raises ValueError naming the first bad one."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        origin = clean_origin(str(item))
        if origin is None:
            raise ValueError(str(item)[:200])
        if origin not in out:
            out.append(origin)
    if len(out) > EMBED_ORIGINS_MAX:
        raise ValueError("too_many")
    return out


def channel_allowed(visibility: str, name: str) -> bool:
    """Whether an audience can carry the channel at all."""
    if name == "embed":
        return visibility == PublicationVisibility.external.value
    return visibility in (PublicationVisibility.link.value, PublicationVisibility.external.value)


def channels_of(pub: Publication) -> dict[str, dict]:
    """The channels in force: opened by an administrator and carried by the audience."""
    return {name: dict(value or {}) for name, value in (pub.channels or {}).items()
            if name in CHANNELS and channel_allowed(pub.visibility, name)}


def channel_open(pub: Publication, name: str) -> bool:
    """Whether a request through the channel may reach the bot now."""
    return pub.status == PublicationStatus.approved.value and name in channels_of(pub)


def embed_origins(pub: Publication) -> list[str]:
    """The sites that may frame the bot now; none unless the embed channel is open."""
    if not channel_open(pub, "embed"):
        return []
    return [o for o in (pub.channels or {}).get("embed", {}).get("origins") or []
            if clean_origin(o) == o][:EMBED_ORIGINS_MAX]


def channels_cover(current: dict | None, wanted: dict) -> bool:
    """Whether `wanted` asks for nothing `current` does not already have (closing a channel or
    dropping a site never needs review)."""
    current = current or {}
    for name, value in wanted.items():
        if name not in current:
            return False
        if name == "embed":
            have = set((current.get("embed") or {}).get("origins") or [])
            if not set((value or {}).get("origins") or []) <= have:
                return False
    return True


def narrow_channels(current: dict | None, wanted: dict) -> dict | None:
    """What of `current` stays when the owner asks for `wanted`: a channel or site closes at
    once; nothing new opens here."""
    out: dict[str, dict] = {}
    for name, value in (current or {}).items():
        if name not in wanted:
            continue
        if name == "embed":
            asked = (wanted.get("embed") or {}).get("origins") or []
            keep = [o for o in (value or {}).get("origins") or [] if o in asked]
            if keep:
                out["embed"] = {"origins": keep}
        else:
            out[name] = {}
    return out or None


def fit_channels(channels: dict | None, visibility: str) -> dict | None:
    """The channels an audience can carry; None when none is left."""
    kept = {name: value for name, value in (channels or {}).items()
            if name in CHANNELS and channel_allowed(visibility, name)}
    return kept or None


# ── keys for the API and MCP channels ──────────────────────────────────


KEY_PREFIX = "kcb_"
KEYS_MAX = 10
_KEY = re.compile(r"kcb_[A-Za-z0-9_-]{40,64}")


def new_key() -> tuple[str, str, str]:
    """A fresh key, its hash (what is stored) and the preview shown afterwards."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key_hash(key), f"{key[:8]}…{key[-4:]}"


def key_hash(key: str) -> str:
    return hashlib.sha256((key or "").encode()).hexdigest()


def key_shaped(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


def key_visitor(key_id: str) -> str:
    """The visitor id a key's calls are kept under: never a cookie's (those have no colon)."""
    return f"key:{key_id}"


def opens_outside(pub: Publication) -> bool:
    """Whether people without an account reach the bot: its versions are then reviewed."""
    return pub.visibility in (PublicationVisibility.link.value,
                              PublicationVisibility.external.value)


def flag_owner(pub: Publication) -> None:
    """Marks something on the bot for its owner to look at (the badge)."""
    pub.attention_at = utcnow()


async def drop_versions(db: AsyncSession, version_rows: list[PublicationVersion]) -> None:
    """Deletes the hidden agent copies and their knowledge text. Caller commits."""
    snaps = [v.agent_snapshot_id for v in version_rows if v.agent_snapshot_id]
    if not snaps:
        return
    await db.exec(delete(StoredFile).where(col(StoredFile.agent_id).in_(snaps)))
    await db.exec(delete(Agent).where(col(Agent.id).in_(snaps),
                                      col(Agent.snapshot_of).is_not(None)))


async def delete_sessions(db: AsyncSession, session_ids: list[str]) -> int:
    """Removes published conversations whole. Caller commits."""
    if not session_ids:
        return 0
    await db.exec(delete(Message).where(col(Message.session_id).in_(session_ids)))
    await db.exec(delete(PipelineRun).where(col(PipelineRun.session_id).in_(session_ids)))
    await db.exec(delete(ChatSession).where(col(ChatSession.id).in_(session_ids),
                                            col(ChatSession.publication_id).is_not(None)))
    return len(session_ids)


def new_slug(title: str) -> str:
    """An address that cannot be guessed from another's: the title's Latin letters when it
    has any (a Korean title would be percent-encoded in every copied link), else `bot-`."""
    base = re.sub(r"[^0-9a-z]+", "-", (title or "").strip().lower()).strip("-")[:30]
    return f"{base or 'bot'}-{secrets.token_hex(4)}"


# ── pausing ────────────────────────────────────────────────────────────


#: Pauses the system makes, lifted by the system when the cause is gone; an owner's or an
#: administrator's pause stays until they lift it.
AUTO_REASONS = ("credits", "account", "model")


def owner_blocks(owner: User | None) -> str:
    """Why the owner's account cannot carry the bot now, or ""."""
    if owner is None or owner.status is not UserStatus.active:
        return "account"
    if owner.credits_remaining <= 0:
        return "credits"
    return ""


def pause(db: AsyncSession, pub: Publication, reason: str, *,
          actor_id: str | None = None) -> None:
    """Pauses the bot and records it where the owner and the administrators look. Caller
    commits."""
    if pub.status == PublicationStatus.paused.value and pub.pause_reason == reason:
        return
    pub.status = PublicationStatus.paused.value
    pub.pause_reason = reason
    pub.updated_at = utcnow()
    if reason != "owner":
        flag_owner(pub)
    db.add(pub)
    db.add(AuditEvent(actor_id=actor_id, action="publication.paused", target=pub.id,
                      detail=reason,
                      event_metadata={"publicationId": pub.id, "reason": reason,
                                      "ownerId": pub.owner_id}))


def resume_if_cleared(db: AsyncSession, pub: Publication, owner: User | None) -> bool:
    """Lifts a pause the system made once its cause is gone. Caller commits."""
    if pub.status != PublicationStatus.paused.value or pub.pause_reason not in AUTO_REASONS:
        return False
    if pub.pause_reason in ("credits", "account") and owner_blocks(owner):
        return False
    if pub.pause_reason == "model":
        return False
    pub.status = PublicationStatus.approved.value
    pub.pause_reason = ""
    pub.updated_at = utcnow()
    db.add(pub)
    db.add(AuditEvent(actor_id=None, action="publication.resumed", target=pub.id,
                      detail="auto", event_metadata={"publicationId": pub.id}))
    return True


# ── the visitor's answer ───────────────────────────────────────────────

#: What a bot says when its material holds nothing for the question: the same sentence as
#: the 「근거 없는 답 보류」 check's, so the owner's statistics count it either way.
NOT_COVERED = "제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다."

#: The system rule of a published bot's turn; the server has searched the material first.
MATERIAL_ONLY_RULE = (
    "Material only: you answer visitors from the material this bot was published with. The "
    "server has already searched it with the question, and the passages are the first "
    "search_knowledge result, numbered like [1]. Answer only from those passages (search "
    "again with other words if they do not hold the answer), and put the passage number at "
    "the end of each sentence that uses one, like [1]. Never answer from general knowledge "
    "or guess a value, time, count or fee the passages do not state. When the material does "
    f"not answer the question, say only 「{NOT_COVERED}」 and give the contact if the "
    "instructions name one."
)


def not_covered_answer(contact: str) -> str:
    """The bot's whole answer when its material search finds nothing for the question."""
    contact = (contact or "").strip()
    return NOT_COVERED + "\n\n" + (f"담당 부서·연락처: {contact}" if contact
                                    else "담당 부서에 문의해 주세요.")


# ── after a turn ───────────────────────────────────────────────────────


#: The answer says the material does not hold it (the bot's own words or the 「근거 없는 답
#: 보류」 check's): counted as an unanswered question for the owner.
_NOT_COVERED = re.compile(
    r"(?:자료|규정|문서|안내\s{0,2}자료)(?:에서|에는|에|로는)?\s{0,3}"
    r"(?:(?:이|그)\s{0,2})?(?:질문에\s{0,2}(?:대한|맞는)\s{0,2})?(?:내용을|답을|근거를)?\s{0,3}"
    r"(?:찾지\s{0,2}못|확인(?:되지|할\s{0,2}수\s{0,2}없)|나와\s{0,2}있지\s{0,2}않|없습니다|없어)"
)


def unanswered(answer: str) -> bool:
    return bool(_NOT_COVERED.search((answer or "")[:4000]))


#: Words that carry no topic: question forms, particles left as words, politeness.
_STOP = frozenset("""
어떻게 무엇 무엇인가요 뭔가요 뭐야 뭐예요 알려줘 알려 주세요 알려주세요 있나요 있어요 있습니까
되나요 하나요 해야 하나요 언제 어디 누구 얼마 얼마나 경우 관련 대한 대해 대해서 그리고 그런데
궁금합니다 궁금해요 질문 문의 혹시 저는 제가 지금 이번 다음 그럼 그러면 하는 하고 해도 하면
수 있는지 있는 없는 같은 위한 위해 정도 방법 어떤 무슨 이거 그거 저거 좀 꼭 다시
""".split())
_PARTICLES = ("에서는", "으로는", "에서", "으로", "에게", "까지", "부터", "이나", "이랑",
              "하고", "에는", "은", "는", "이", "가", "을", "를", "에", "의", "도", "로",
              "와", "과", "만", "요")
_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,20}")


def topics(question: str, limit: int = 3) -> list[str]:
    """A question's topic words, personal details masked first: what the owner sees counted."""
    text, _ = governance.mask((question or "")[:1000], scope="egress")
    out: list[str] = []
    for word in _WORD.findall(text):
        for particle in _PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                word = word[: -len(particle)]
                break
        if word.isdigit() or word in _STOP or len(word) < 2 or word in out:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


def masked(text: str, limit: int = 300) -> str:
    """A sentence as the owner may read it: personal details and secrets taken out."""
    return governance.mask(" ".join((text or "").split())[:limit], scope="egress")[0]


async def after_turn(db: AsyncSession, turn: PublishedTurn, *, question: str, answer: str,
                     usage: dict[str, Any], failed: bool) -> None:
    """Counts the turn for the owner's statistics. Caller commits."""
    day = day_window()
    tokens = int(usage.get("inputTokens") or 0) + int(usage.get("outputTokens") or 0)
    await bump(db, turn.publication_id, day, "tokens", tokens)
    if failed or not answer.strip():
        await bump(db, turn.publication_id, day, "failed")
        return
    await bump(db, turn.publication_id, day, "answers")
    for word in topics(question):
        await bump(db, turn.publication_id, day, f"topic:{word}")
    if unanswered(answer):
        await bump(db, turn.publication_id, day, "unanswered")
        db.add(PublicationQuestion(publication_id=turn.publication_id,
                                   version_id=turn.version_id, text=masked(question)))


# ── what the owner and an administrator may read ───────────────────────


def owner_may_read(session: ChatSession, pub: Publication) -> bool:
    """A conversation the visitor sent, or one started under 「대화 열람」 while it is on."""
    if session.publication_id != pub.id:
        return False
    return session.shared_at is not None or (pub.conversation_view == "on" and session.viewable)


def masked_transcript(messages: list[Message]) -> list[dict[str, Any]]:
    """The conversation with personal details masked and the working left out."""
    return [
        {
            "id": m.id,
            "role": str(getattr(m.role, "value", m.role)),
            "content": governance.mask(m.content or "", scope="egress")[0],
            "rating": str(getattr(m.rating, "value", m.rating)) if m.rating else None,
            "createdAt": m.created_at,
        }
        for m in messages
        if m.role in (Role.user, Role.assistant)
    ]


# ── retention ──────────────────────────────────────────────────────────


async def sweep(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Deletes each bot's conversations and unanswered questions past its retention, old
    minute counters and stale slots. Commits."""
    now = now or utcnow()
    out = {"sessions": 0, "questions": 0}
    for pub in (await db.exec(select(Publication))).all():
        cutoff = now - timedelta(days=max(1, int(pub.retention_days or 30)))
        old = list((await db.exec(select(ChatSession.id).where(
            ChatSession.publication_id == pub.id, col(ChatSession.updated_at) < cutoff))).all())
        out["sessions"] += await delete_sessions(db, old)
        # A visitor without an account who has not come back within the retention is
        # forgotten too (their conversations went above); a blocked one is kept.
        await db.exec(delete(PublicationVisitor).where(
            PublicationVisitor.publication_id == pub.id, col(PublicationVisitor.user_id).is_(None),
            col(PublicationVisitor.blocked).is_(False),
            col(PublicationVisitor.last_seen_at) < cutoff))
        gone = (await db.exec(select(func.count()).select_from(PublicationQuestion).where(
            PublicationQuestion.publication_id == pub.id,
            col(PublicationQuestion.at) < cutoff))).one()
        await db.exec(delete(PublicationQuestion).where(
            PublicationQuestion.publication_id == pub.id, col(PublicationQuestion.at) < cutoff))
        out["questions"] += int(gone or 0)
    await db.exec(delete(PublicationCounter).where(
        col(PublicationCounter.window).like("m%"),
        col(PublicationCounter.window) < minute_window(now - _MINUTE_KEEP)))
    await db.exec(delete(PublicationCounter).where(
        col(PublicationCounter.window).like("d%"),
        col(PublicationCounter.window) < day_window(now - _DAY_KEEP)))
    await db.exec(delete(PublicationSlot).where(
        col(PublicationSlot.acquired_at) < now - timedelta(seconds=SLOT_TTL_SEC)))
    await db.commit()
    if out["sessions"] or out["questions"]:
        log.info("publishing retention: %d conversations, %d questions removed",
                 out["sessions"], out["questions"])
    return out


async def check_owners(db: AsyncSession) -> int:
    """Pauses bots whose owner can no longer carry them and lifts the system's pauses whose
    cause is gone. Commits; returns how many changed."""
    changed = 0
    rows = (await db.exec(select(Publication).where(col(Publication.status).in_(
        [PublicationStatus.approved.value, PublicationStatus.paused.value])))).all()
    for pub in rows:
        owner = await db.get(User, pub.owner_id)
        reason = owner_blocks(owner)
        if pub.status == PublicationStatus.approved.value and reason:
            pause(db, pub, reason)
            changed += 1
        elif resume_if_cleared(db, pub, owner):
            changed += 1
    await db.commit()
    return changed


_INTERVAL = 30 * 60


async def watch() -> None:
    """The boot-time loop: retention and owner checks every half hour."""
    while True:
        try:
            async with core_db.SessionLocal() as db:
                await sweep(db)
                await check_owners(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the next round tries again
            log.warning("publishing sweep failed: %s", logs.safe(exc))
        await asyncio.sleep(_INTERVAL)


__all__ = [
    "CHANNELS",
    "CHANNEL_NAMES",
    "KEYS_MAX",
    "channel_allowed",
    "channel_open",
    "channels_cover",
    "channels_of",
    "clean_origin",
    "clean_origins",
    "embed_origins",
    "fit_channels",
    "key_hash",
    "key_shaped",
    "key_visitor",
    "narrow_channels",
    "new_key",
    "ANON_CONVERSATIONS_PER_IP_DAY",
    "ANON_VISITORS_PER_IP_DAY",
    "CREDITS_PER_DAY",
    "DEFAULT_LIMITS",
    "PUBLISHABLE_TOOLS",
    "EMBED_VISITOR_COOKIE",
    "VISITOR_COOKIE",
    "PublishedTurn",
    "Refused",
    "acquire_slot",
    "admit",
    "admit_anonymous",
    "after_turn",
    "bump",
    "check_owners",
    "clean_limits",
    "credits_cap",
    "credits_today",
    "day_window",
    "delete_sessions",
    "drop_versions",
    "flag_owner",
    "holding",
    "ip_key",
    "limits_of",
    "make_version",
    "masked",
    "masked_transcript",
    "minute_window",
    "new_slug",
    "new_visitor_cookie",
    "opens_outside",
    "owner_blocks",
    "owner_may_read",
    "pause",
    "pick_model",
    "preflight",
    "release_slot",
    "resume_if_cleared",
    "sweep",
    "topics",
    "unanswered",
    "visitor_from_cookie",
    "watch",
]
