"""Published agents (공개 챗봇): the owner's screens, the bot page, and the administrator's
review.

A bot opens to the members of this instance as soon as it is published. Opening it to people
without an account — by link (`link`) or listed on the public page (`external`) — is asked of
an administrator, who approves the audience and the version together; on a bot open outside,
each new version waits for the same review while the approved one keeps answering.

- `/publications…` — the owner: preflight, publish, the audience and its review, versions and
  rollback, settings, pause, statistics, blocking a visitor, and the conversations visitors
  sent them (or, with 「대화 열람」 on, those started after the visitor's consent), personal
  details masked.
- `/bots`, `/public/bots`, `/b/{slug}…` — visitors: the members' list, the public list, a
  bot's page, the notice and consent, their own conversations with it, 👍👎 and 「만든
  사람에게 보내기」. A visitor without an account is a signed cookie and nothing more. A
  visitor's turn is the ordinary turn (`sessions.send_message`) run on the owner's account
  under the bot's fixed version (`services.publishing`).
- `/admin/publications…` — administrators: the review queue, approving or refusing a request
  (a refusal's reason reaches the owner), closing an outside audience, pausing or closing a
  bot, and reading a conversation for an abuse review, with a reason that is recorded.

A bot open outside reaches further through channels the owner asks for and an administrator
opens with the audience (`/publications/{id}/channels`): framed on the owner's sites (the
embed page sends `X-KloudChat-Embed` and keeps its own partitioned cookie), and the API and
MCP channels in `routers/bot_api`, called with a key the owner issued for the bot
(`/publications/{id}/keys`). Closing the outside audience closes every channel with it.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func
from sqlmodel import col, delete, select

from app.core import logs
from app.core.config import settings
from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.core.security import decode_access_token
from app.models.chat import ChatSession, Message, Role, RoutingMode, SessionKind
from app.models.publishing import (
    VISIBILITY_RANK,
    Publication,
    PublicationCounter,
    PublicationKey,
    PublicationQuestion,
    PublicationSlot,
    PublicationStatus,
    PublicationVersion,
    PublicationVisibility,
    PublicationVisitor,
    ReviewState,
)
from app.models.user import AuditEvent, User, UserRole, UserStatus, utcnow
from app.models.workspace import Agent, StoredFile
from app.schemas.chat import SendMessage
from app.schemas.publishing import (
    AdminStatusIn,
    ChannelsIn,
    ConsentIn,
    KeyIn,
    PreflightIn,
    PublicationPatch,
    PublicationSettings,
    PublishIn,
    ReviewIn,
    ScopeIn,
    VersionIn,
    VisitorMessage,
    VisitorRating,
    WithdrawIn,
)
from app.services import governance, publishing, stop_signal
from app.services import models as model_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["publications"])

_STEP_KEEP = ("id", "label", "status", "category")
_SOURCES = re.compile(r"\n{1,3}#{2,3} ?출처[ \t]{0,4}\n[\s\S]{0,8000}$")


# ── shared pieces ──────────────────────────────────────────────────────


def _branding(raw: dict | None) -> dict[str, Any]:
    raw = raw or {}
    prompts = [" ".join(str(p).split())[:200] for p in raw.get("suggestedPrompts") or []
               if str(p).strip()][:4]
    return {"greeting": str(raw.get("greeting") or "")[:400],
            "suggestedPrompts": prompts,
            "placeholder": str(raw.get("placeholder") or "")[:120]}


def _apply(pub: Publication, payload: PublicationSettings) -> None:
    """The owner's settings; what changes the notice raises the consent version."""
    fields = payload.model_fields_set
    if "title" in fields and payload.title is not None:
        pub.title = " ".join(payload.title.split())[:80]
    if "description" in fields and payload.description is not None:
        pub.description = payload.description.strip()[:400]
    if "branding" in fields and payload.branding is not None:
        pub.branding = _branding(payload.branding.model_dump(by_alias=True))
    if "limits" in fields and payload.limits is not None:
        pub.limits = publishing.clean_limits(payload.limits) or None
    if "show_process" in fields and payload.show_process is not None:
        pub.show_process = payload.show_process
    if "show_sources" in fields and payload.show_sources is not None:
        pub.show_sources = payload.show_sources
    notice_changed = False
    if "retention_days" in fields and payload.retention_days is not None \
            and payload.retention_days != pub.retention_days:
        pub.retention_days = payload.retention_days
        notice_changed = True
    if "conversation_view" in fields and payload.conversation_view is not None \
            and payload.conversation_view != pub.conversation_view:
        pub.conversation_view = payload.conversation_view
        notice_changed = True
    if notice_changed:
        # Visitors read the new notice before their next question.
        pub.consent_version = (pub.consent_version or 1) + 1
    pub.updated_at = utcnow()


def _limits_out(pub: Publication) -> dict[str, Any]:
    return {"limits": publishing.limits_of(pub), "defaults": dict(publishing.DEFAULT_LIMITS)}


def _version_out(v: PublicationVersion, active: str | None,
                 pending: str | None = None) -> dict[str, Any]:
    return {"id": v.id, "number": v.number, "model": v.model, "note": v.note,
            "summary": v.summary or {}, "active": v.id == active, "pending": v.id == pending,
            "approvedAt": v.approved_at, "createdAt": v.created_at}


def _needs_look(pub: Publication) -> bool:
    """Something happened on the bot since its owner last opened its page."""
    if pub.attention_at is None:
        return False
    if pub.owner_seen_at is None:
        return True
    # Stored in UTC; a driver may hand them back without the zone.
    return pub.attention_at.replace(tzinfo=None) > pub.owner_seen_at.replace(tzinfo=None)


async def _versions(db: DbSession, pub: Publication) -> list[PublicationVersion]:
    return list((await db.exec(select(PublicationVersion).where(
        PublicationVersion.publication_id == pub.id).order_by(
        col(PublicationVersion.number).desc()))).all())


def _pub_out(pub: Publication, *, versions: list[PublicationVersion] | None = None,
             agent_name: str = "", owner_name: str = "") -> dict[str, Any]:
    out = {
        "id": pub.id, "slug": pub.slug, "title": pub.title, "description": pub.description,
        "agentId": pub.agent_id, "agentName": agent_name, "ownerName": owner_name,
        "visibility": pub.visibility, "status": pub.status, "pauseReason": pub.pause_reason,
        "activeVersionId": pub.active_version_id, "branding": _branding(pub.branding),
        "showProcess": pub.show_process, "showSources": pub.show_sources,
        "retentionDays": pub.retention_days, "conversationView": pub.conversation_view,
        "consentVersion": pub.consent_version, **_limits_out(pub),
        "requestedVisibility": pub.requested_visibility, "reviewState": pub.review_state,
        "reviewNote": pub.review_note, "reviewedAt": pub.reviewed_at,
        "pendingVersionId": pub.pending_version_id, "needsLook": _needs_look(pub),
        "channels": publishing.channels_of(pub),
        "requestedChannels": pub.requested_channels,
        "createdAt": pub.created_at, "updatedAt": pub.updated_at,
        "publishedAt": pub.published_at,
    }
    if versions is not None:
        out["versions"] = [_version_out(v, pub.active_version_id, pub.pending_version_id)
                           for v in versions]
    return out


async def _mine(db: DbSession, user: User, publication_id: str) -> Publication:
    pub = await db.get(Publication, publication_id)
    if pub is None or pub.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return pub


async def _own_agent(db: DbSession, user: User, agent_id: str) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != user.id or agent.snapshot_of:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent_not_found")
    return agent


async def _model_context(user: User) -> tuple[list[dict], list[str]]:
    catalogue = await model_service.list_models_for_egress()
    try:
        policy = await governance.current_for_egress()
        safe = list(policy.privacy_safe_model_ids or [])
    except governance.GovernanceUnavailable:
        safe = []
    return catalogue["models"], safe


async def _unique_slug(db: DbSession, title: str) -> str:
    for _ in range(8):
        slug = publishing.new_slug(title)
        if (await db.exec(select(Publication.id).where(Publication.slug == slug))).first() is None:
            return slug
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slug_taken")


def _audit(db: DbSession, actor: User | None, action: str, pub: Publication, request: Request,
           *, severity: str = "info", **metadata: Any) -> None:
    db.add(AuditEvent(actor_id=actor.id if actor else None, action=action, target=pub.id,
                      detail=pub.title[:120], severity=severity,
                      event_metadata={"publicationId": pub.id, **metadata},
                      ip=client_ip(request),
                      user_agent=request.headers.get("User-Agent", "")[:400]))


async def _remove(db: DbSession, pub: Publication) -> None:
    """The bot and everything it holds: conversations, versions and their copies, counts."""
    ids = list((await db.exec(select(ChatSession.id).where(
        ChatSession.publication_id == pub.id))).all())
    await publishing.delete_sessions(db, ids)
    await publishing.drop_versions(db, await _versions(db, pub))
    for model in (PublicationVersion, PublicationVisitor, PublicationQuestion, PublicationSlot,
                  PublicationCounter, PublicationKey):
        await db.exec(delete(model).where(model.publication_id == pub.id))
    await db.delete(pub)


# ── the owner ──────────────────────────────────────────────────────────


@router.get("/publications")
async def list_publications(user: CurrentUser, db: DbSession):
    rows = (await db.exec(select(Publication).where(Publication.owner_id == user.id)
                          .order_by(col(Publication.updated_at).desc()))).all()
    agents = {a.id: a.name for a in (await db.exec(select(Agent).where(
        col(Agent.id).in_([p.agent_id for p in rows])))).all()} if rows else {}
    today = publishing.day_window()
    turns = dict((await db.exec(select(PublicationCounter.publication_id,
                                       PublicationCounter.value).where(
        col(PublicationCounter.publication_id).in_([p.id for p in rows]),
        PublicationCounter.window == today, PublicationCounter.key == "turns"))).all()) \
        if rows else {}
    return [{**_pub_out(p, agent_name=agents.get(p.agent_id, "")),
             "turnsToday": int(turns.get(p.id, 0))} for p in rows]


@router.get("/publications/attention")
async def attention(user: CurrentUser, db: DbSession):
    """The badges: the caller's bots with something new to look at, and for an administrator
    the requests waiting for review."""
    mine = (await db.exec(select(Publication).where(
        Publication.owner_id == user.id, col(Publication.attention_at).is_not(None)))).all()
    reviews = 0
    if user.role is UserRole.admin:
        reviews = int((await db.exec(select(func.count()).select_from(Publication).where(
            Publication.review_state == ReviewState.pending.value))).one())
    return {"bots": sum(1 for p in mine if _needs_look(p)), "reviews": reviews}


def _request(pub: Publication, visibility: str) -> None:
    """Asks an administrator for a wider audience. Caller audits and commits."""
    pub.requested_visibility = visibility
    pub.review_state = ReviewState.pending.value
    pub.review_note = ""
    pub.updated_at = utcnow()


def _require_cap(pub: Publication) -> None:
    if publishing.credits_cap(pub) is None:
        raise HTTPException(status_code=422, detail="credits_cap_required")


@router.post("/publications/preflight")
async def preflight(payload: PreflightIn, user: CurrentUser, db: DbSession):
    """Whether the agent can be published now, the internal model it would answer with, what
    a bot leaves out, and personal details found in its knowledge."""
    agent = await _own_agent(db, user, payload.agent_id)
    catalogue, safe = await _model_context(user)
    existing = (await db.exec(select(Publication).where(
        Publication.owner_id == user.id, Publication.agent_id == agent.id,
        Publication.status != PublicationStatus.revoked.value))).first()
    out = await publishing.preflight(db, user, agent, catalogue, safe)
    return {**out, "existingId": existing.id if existing else None,
            "defaults": dict(publishing.DEFAULT_LIMITS)}


@router.post("/publications", status_code=status.HTTP_201_CREATED)
async def publish(payload: PublishIn, request: Request, user: CurrentUser, db: DbSession):
    """Publishes the agent to members as version 1; a wider audience is asked of an
    administrator at the same time."""
    agent = await _own_agent(db, user, payload.agent_id)
    existing = (await db.exec(select(Publication).where(
        Publication.owner_id == user.id, Publication.agent_id == agent.id,
        Publication.status != PublicationStatus.revoked.value))).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "already_published", "id": existing.id})
    catalogue, safe = await _model_context(user)
    checked = await publishing.preflight(db, user, agent, catalogue, safe)
    if not checked["ok"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "cannot_publish", "problems": checked["problems"]})
    title = " ".join((payload.title or agent.name).split())[:80] or agent.name
    pub = Publication(owner_id=user.id, agent_id=agent.id, slug=await _unique_slug(db, title),
                      title=title, description=agent.description[:400],
                      visibility=PublicationVisibility.internal.value,
                      status=PublicationStatus.approved.value, published_at=utcnow(),
                      branding=_branding({"greeting": agent.guide[:400],
                                          "suggestedPrompts": list(agent.starters or [])}))
    _apply(pub, payload)
    pub.consent_version = 1
    if payload.visibility != PublicationVisibility.internal.value:
        _require_cap(pub)
        _request(pub, payload.visibility)
    db.add(pub)
    await db.flush()
    version = await publishing.make_version(db, user, pub, agent, checked["model"]["id"],
                                            payload.note)
    _audit(db, user, "publication.published", pub, request, version=version.number,
           model=version.model)
    if pub.review_state == ReviewState.pending.value:
        _audit(db, user, "publication.requested", pub, request,
               visibility=pub.requested_visibility, version=version.number)
    await db.commit()
    return _pub_out(pub, versions=await _versions(db, pub), agent_name=agent.name)


@router.get("/publications/{publication_id}")
async def get_publication(publication_id: str, user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    agent = await db.get(Agent, pub.agent_id) if pub.agent_id else None
    out = _pub_out(pub, versions=await _versions(db, pub),
                   agent_name=agent.name if agent is not None else "")
    # The owner has now seen what changed: the badge clears.
    pub.owner_seen_at = utcnow()
    db.add(pub)
    await db.commit()
    return out


@router.patch("/publications/{publication_id}")
async def patch_publication(publication_id: str, payload: PublicationPatch, request: Request,
                            user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    _apply(pub, payload)
    outside = publishing.opens_outside(pub) or pub.review_state == ReviewState.pending.value
    if outside:
        # Open (or asked to open) outside: the daily credit cap stays.
        _require_cap(pub)
        if payload.model_fields_set - {"status"}:
            _audit(db, user, "publication.settings", pub, request,
                   fields=sorted(payload.model_fields_set - {"status"}))
    if payload.status == PublicationStatus.paused.value \
            and pub.status != PublicationStatus.paused.value:
        publishing.pause(db, pub, "owner", actor_id=user.id)
    elif payload.status == PublicationStatus.approved.value \
            and pub.status == PublicationStatus.paused.value:
        if pub.pause_reason == "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="paused_by_admin")
        if reason := publishing.owner_blocks(user):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "cannot_resume", "reason": reason})
        pub.status, pub.pause_reason = PublicationStatus.approved.value, ""
        _audit(db, user, "publication.resumed", pub, request)
    db.add(pub)
    await db.commit()
    return await get_publication(publication_id, user, db)


@router.delete("/publications/{publication_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_publication(publication_id: str, request: Request, user: CurrentUser,
                             db: DbSession):
    """Closes the bot for good: its conversations, versions and statistics go with it."""
    pub = await _mine(db, user, publication_id)
    _audit(db, user, "publication.deleted", pub, request)
    await _remove(db, pub)
    await db.commit()


@router.post("/publications/{publication_id}/scope")
async def set_scope(publication_id: str, payload: ScopeIn, request: Request, user: CurrentUser,
                    db: DbSession):
    """The audience: a narrower one applies at once; a wider one is asked of an administrator
    (and needs a daily credit cap), while the bot keeps its present audience."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    if payload.credits_per_day is not None:
        pub.limits = publishing.clean_limits(
            {**(pub.limits or {}), publishing.CREDITS_PER_DAY: payload.credits_per_day})
    target = payload.visibility
    if VISIBILITY_RANK[target] > VISIBILITY_RANK[pub.visibility]:
        _require_cap(pub)
        _request(pub, target)
        _audit(db, user, "publication.requested", pub, request, visibility=target)
    else:
        if target != pub.visibility:
            _audit(db, user, "publication.narrowed", pub, request, visibility=target,
                   previous=pub.visibility)
        pub.visibility = target
        pub.requested_visibility = ""
        # A channel the narrower audience cannot carry closes with it, asked or open.
        pub.channels = publishing.fit_channels(pub.channels, target)
        pub.requested_channels = publishing.fit_channels(pub.requested_channels, target)
        if target == PublicationVisibility.internal.value and pub.pending_version_id:
            # Members only: a version waiting for review needs none, so it answers now.
            pub.active_version_id, pub.pending_version_id = pub.pending_version_id, None
        if pub.pending_version_id is None and not pub.requested_channels:
            pub.review_state = ReviewState.none.value
        pub.updated_at = utcnow()
    db.add(pub)
    await db.commit()
    return await get_publication(publication_id, user, db)


@router.delete("/publications/{publication_id}/review")
async def withdraw_request(publication_id: str, request: Request, user: CurrentUser,
                           db: DbSession):
    """Takes back a request: the wider audience, and the version waiting with it."""
    pub = await _mine(db, user, publication_id)
    if pub.review_state:
        _audit(db, user, "publication.request_withdrawn", pub, request,
               visibility=pub.requested_visibility)
    pub.requested_visibility = ""
    pub.requested_channels = None
    pub.pending_version_id = None
    pub.review_state = ReviewState.none.value
    pub.updated_at = utcnow()
    db.add(pub)
    await db.commit()
    return await get_publication(publication_id, user, db)


@router.post("/publications/{publication_id}/versions", status_code=status.HTTP_201_CREATED)
async def new_version(publication_id: str, payload: VersionIn, request: Request,
                      user: CurrentUser, db: DbSession):
    """Publishes the agent as it stands now as the next version. On a bot open outside it
    waits for review; the approved version answers meanwhile."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    agent = await _own_agent(db, user, pub.agent_id)
    catalogue, safe = await _model_context(user)
    checked = await publishing.preflight(db, user, agent, catalogue, safe)
    if not checked["ok"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "cannot_publish", "problems": checked["problems"]})
    reviewed = publishing.opens_outside(pub)
    version = await publishing.make_version(db, user, pub, agent, checked["model"]["id"],
                                            payload.note, activate=not reviewed)
    if reviewed:
        pub.review_state = ReviewState.pending.value
        pub.review_note = ""
    _audit(db, user, "publication.version", pub, request, version=version.number,
           pending=reviewed)
    await db.commit()
    return await get_publication(publication_id, user, db)


@router.post("/publications/{publication_id}/versions/{version_id}/activate")
async def activate_version(publication_id: str, version_id: str, request: Request,
                           user: CurrentUser, db: DbSession):
    """Rolls the bot back (or forward) to a version it had. On a bot open outside, a version
    no administrator approved waits for review instead."""
    pub = await _mine(db, user, publication_id)
    version = await db.get(PublicationVersion, version_id)
    if version is None or version.publication_id != pub.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version_not_found")
    reviewed = publishing.opens_outside(pub) and version.approved_at is None
    if reviewed:
        pub.pending_version_id = version.id
        pub.review_state = ReviewState.pending.value
        pub.review_note = ""
    else:
        pub.active_version_id = version.id
    pub.updated_at = utcnow()
    db.add(pub)
    _audit(db, user, "publication.rollback", pub, request, version=version.number,
           pending=reviewed)
    await db.commit()
    return await get_publication(publication_id, user, db)


# ── channels and keys ──────────────────────────────────────────────────


@router.post("/publications/{publication_id}/channels")
async def set_channels(publication_id: str, payload: ChannelsIn, request: Request,
                       user: CurrentUser, db: DbSession):
    """The channels the owner wants, as a whole set. Closing one (or dropping a site) applies
    at once; opening one is asked of an administrator with the audience it needs, and the
    daily credit cap stays required."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    wanted: dict[str, dict] = {}
    if payload.embed is not None and payload.embed.on:
        try:
            origins = publishing.clean_origins(payload.embed.origins)
        except ValueError as bad:
            raise HTTPException(status_code=422,
                                detail={"code": "bad_origin", "origin": str(bad)}) from None
        if not origins:
            raise HTTPException(status_code=422, detail="origins_required")
        wanted["embed"] = {"origins": origins}
    if payload.api:
        wanted["api"] = {}
    if payload.mcp:
        wanted["mcp"] = {}
    # The audience in force, or the one asked for alongside: a channel needs one to carry it.
    audience = pub.requested_visibility or pub.visibility
    for name in wanted:
        if not publishing.channel_allowed(audience, name):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail={"code": "channel_needs_audience", "channel": name})
    before = publishing.channels_of(pub)
    current = pub.channels
    pub.channels = publishing.narrow_channels(current, wanted)
    if publishing.channels_cover(current, wanted):
        pub.requested_channels = None
        if pub.review_state == ReviewState.pending.value and not pub.requested_visibility \
                and pub.pending_version_id is None:
            pub.review_state = ReviewState.none.value
        if publishing.channels_of(pub) != before:
            _audit(db, user, "publication.channels_closed", pub, request,
                   channels=sorted(publishing.channels_of(pub)))
    else:
        _require_cap(pub)
        pub.requested_channels = wanted
        pub.review_state = ReviewState.pending.value
        pub.review_note = ""
        _audit(db, user, "publication.channels_requested", pub, request,
               channels=sorted(wanted),
               origins=(wanted.get("embed") or {}).get("origins") or [])
    pub.updated_at = utcnow()
    db.add(pub)
    await db.commit()
    return await get_publication(publication_id, user, db)


def _key_out(row: PublicationKey) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "preview": row.preview, "createdAt": row.created_at,
            "lastUsedAt": row.last_used_at}


@router.get("/publications/{publication_id}/keys")
async def list_keys(publication_id: str, user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    rows = (await db.exec(select(PublicationKey).where(
        PublicationKey.publication_id == pub.id, col(PublicationKey.revoked_at).is_(None))
        .order_by(col(PublicationKey.created_at).desc()))).all()
    return [_key_out(k) for k in rows]


@router.post("/publications/{publication_id}/keys", status_code=status.HTTP_201_CREATED)
async def create_key(publication_id: str, payload: KeyIn, request: Request, user: CurrentUser,
                     db: DbSession):
    """A key for this bot's API and MCP channels, returned once; only its hash is kept."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    live = int((await db.exec(select(func.count()).select_from(PublicationKey).where(
        PublicationKey.publication_id == pub.id,
        col(PublicationKey.revoked_at).is_(None)))).one())
    if live >= publishing.KEYS_MAX:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="too_many_keys")
    key, hashed, preview = publishing.new_key()
    row = PublicationKey(publication_id=pub.id, name=" ".join(payload.name.split())[:80],
                         key_hash=hashed, preview=preview, created_by=user.id)
    db.add(row)
    _audit(db, user, "publication.key_created", pub, request, keyId=row.id, name=row.name)
    await db.commit()
    # The only response that carries the key.
    return {**_key_out(row), "key": key}


@router.delete("/publications/{publication_id}/keys/{key_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(publication_id: str, key_id: str, request: Request, user: CurrentUser,
                     db: DbSession):
    """Stops the key at once; kept as a revoked row for the audit trail."""
    pub = await _mine(db, user, publication_id)
    row = await db.get(PublicationKey, key_id)
    if row is None or row.publication_id != pub.id or row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key_not_found")
    row.revoked_at = utcnow()
    db.add(row)
    _audit(db, user, "publication.key_revoked", pub, request, keyId=row.id, name=row.name)
    await db.commit()


@router.get("/publications/{publication_id}/admin-reads")
async def admin_reads(publication_id: str, user: CurrentUser, db: DbSession):
    """When an administrator read the bot's conversations, and why: the owner sees each one."""
    pub = await _mine(db, user, publication_id)
    rows = (await db.exec(select(AuditEvent).where(
        AuditEvent.target == pub.id,
        col(AuditEvent.action).in_(["publication.admin_list", "publication.admin_read"]))
        .order_by(col(AuditEvent.at).desc()).limit(100))).all()
    return [{"at": e.at, "kind": "list" if e.action == "publication.admin_list" else "read",
             "reason": str((e.event_metadata or {}).get("reason") or "")} for e in rows]


def _days(days: int) -> list[str]:
    now = utcnow()
    return [publishing.day_window(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]


@router.get("/publications/{publication_id}/stats")
async def stats(publication_id: str, user: CurrentUser, db: DbSession,
                days: int = Query(30, ge=1, le=90)):
    """Usage, frequent topics, unanswered questions (masked) and 👍👎 — never conversations."""
    pub = await _mine(db, user, publication_id)
    windows = _days(days)
    rows = (await db.exec(select(PublicationCounter).where(
        PublicationCounter.publication_id == pub.id,
        col(PublicationCounter.window).in_(windows)))).all()
    keys = ("turns", "answers", "conversations", "visitors", "tokens", "unanswered", "up",
            "down", "shared", "failed")
    by_day: dict[str, dict[str, int]] = {w: dict.fromkeys(keys, 0) for w in windows}
    topics: dict[str, int] = {}
    by_channel: dict[str, int] = dict.fromkeys(publishing.CHANNEL_NAMES, 0)
    for row in rows:
        if row.key.startswith("topic:"):
            topics[row.key[6:]] = topics.get(row.key[6:], 0) + int(row.value)
        elif row.key.startswith("channel:") and row.key[8:] in by_channel:
            by_channel[row.key[8:]] += int(row.value)
        elif row.key in keys:
            by_day[row.window][row.key] += int(row.value)
    totals = {k: sum(d[k] for d in by_day.values()) for k in keys}
    questions = (await db.exec(select(PublicationQuestion).where(
        PublicationQuestion.publication_id == pub.id).order_by(
        col(PublicationQuestion.at).desc()).limit(publishing.UNANSWERED_SHOWN))).all()
    visitors = int((await db.exec(select(func.count()).select_from(PublicationVisitor).where(
        PublicationVisitor.publication_id == pub.id))).one())
    return {
        "days": days,
        "daily": [{"date": f"{w[1:5]}-{w[5:7]}-{w[7:9]}", **d} for w, d in by_day.items()],
        "totals": {**totals, "visitorsAll": visitors},
        # Questions by where they came from: the bot's page, a site framing it, API, MCP.
        "channels": by_channel,
        "topics": [{"word": w, "count": c}
                   for w, c in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))[:15]],
        "unanswered": [{"text": q.text, "at": q.at} for q in questions],
    }


def _visitor_out(visitor: PublicationVisitor | None) -> dict[str, Any] | None:
    """A visitor as the owner may know them: a short label, whether they signed in, whether
    they are blocked — never who they are."""
    if visitor is None:
        return None
    return {"id": visitor.id, "label": visitor.id[:6].upper(),
            "member": visitor.user_id is not None, "blocked": visitor.blocked,
            "blockedAt": visitor.blocked_at,
            # A key of the owner's (API or MCP) rather than a person.
            "byKey": (visitor.anon_id or "").startswith("key:")}


def _conversation_row(session: ChatSession, turns: int,
                      visitor: PublicationVisitor | None = None) -> dict[str, Any]:
    return {"id": session.id, "title": publishing.masked(session.title or "", 80),
            "sharedAt": session.shared_at, "viewable": session.viewable,
            "updatedAt": session.updated_at, "createdAt": session.created_at, "turns": turns,
            "visitor": _visitor_out(visitor)}


async def _visitors_of(db: DbSession,
                       rows: list[ChatSession]) -> dict[str, PublicationVisitor]:
    ids = {s.visitor_id for s in rows if s.visitor_id}
    if not ids:
        return {}
    return {v.id: v for v in (await db.exec(select(PublicationVisitor).where(
        col(PublicationVisitor.id).in_(ids)))).all()}


async def _turn_counts(db: DbSession, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    return dict((await db.exec(select(Message.session_id, func.count()).where(
        col(Message.session_id).in_(ids), Message.role == Role.user).group_by(
        col(Message.session_id)))).all())


@router.get("/publications/{publication_id}/conversations")
async def owner_conversations(publication_id: str, user: CurrentUser, db: DbSession):
    """The conversations the owner may read: those a visitor sent, and with 「대화 열람」 on,
    those started after the visitor's consent."""
    pub = await _mine(db, user, publication_id)
    query = select(ChatSession).where(ChatSession.publication_id == pub.id)
    rows = [s for s in (await db.exec(query.order_by(col(ChatSession.updated_at).desc())
                                      .limit(500))).all() if publishing.owner_may_read(s, pub)]
    rows = rows[:200]
    counts = await _turn_counts(db, [s.id for s in rows])
    visitors = await _visitors_of(db, rows)
    return [_conversation_row(s, int(counts.get(s.id, 0)), visitors.get(s.visitor_id or ""))
            for s in rows]


@router.get("/publications/{publication_id}/conversations/{session_id}")
async def owner_conversation(publication_id: str, session_id: str, request: Request,
                             user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    session = await db.get(ChatSession, session_id)
    if session is None or not publishing.owner_may_read(session, pub):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    messages = (await db.exec(select(Message).where(Message.session_id == session.id)
                              .order_by(col(Message.created_at), col(Message.id)))).all()
    _audit(db, user, "publication.conversation_read", pub, request, sessionId=session.id,
           shared=session.shared_at is not None)
    visitor = await db.get(PublicationVisitor, session.visitor_id) if session.visitor_id else None
    await db.commit()
    return {**_conversation_row(session, sum(1 for m in messages if m.role is Role.user),
                                visitor),
            "messages": publishing.masked_transcript(list(messages))}


async def _set_blocked(db: DbSession, pub: Publication, visitor: PublicationVisitor,
                       blocked: bool, user: User, request: Request) -> None:
    visitor.blocked = blocked
    visitor.blocked_at = utcnow() if blocked else None
    db.add(visitor)
    _audit(db, user, "publication.visitor_blocked" if blocked else "publication.visitor_unblocked",
           pub, request, visitorId=visitor.id)
    await db.commit()


@router.post("/publications/{publication_id}/conversations/{session_id}/block")
async def block_visitor(publication_id: str, session_id: str, request: Request,
                        user: CurrentUser, db: DbSession):
    """Blocks the visitor of a conversation the owner may read: they can no longer ask."""
    pub = await _mine(db, user, publication_id)
    session = await db.get(ChatSession, session_id)
    if session is None or not publishing.owner_may_read(session, pub) or not session.visitor_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    visitor = await db.get(PublicationVisitor, session.visitor_id)
    if visitor is None or visitor.publication_id != pub.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _set_blocked(db, pub, visitor, True, user, request)
    return _visitor_out(visitor)


@router.get("/publications/{publication_id}/visitors")
async def blocked_visitors(publication_id: str, user: CurrentUser, db: DbSession):
    """The visitors the owner blocked."""
    pub = await _mine(db, user, publication_id)
    rows = (await db.exec(select(PublicationVisitor).where(
        PublicationVisitor.publication_id == pub.id, col(PublicationVisitor.blocked).is_(True))
        .order_by(col(PublicationVisitor.blocked_at).desc()).limit(200))).all()
    return [_visitor_out(v) for v in rows]


@router.delete("/publications/{publication_id}/visitors/{visitor_id}/block")
async def unblock_visitor(publication_id: str, visitor_id: str, request: Request,
                          user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    visitor = await db.get(PublicationVisitor, visitor_id)
    if visitor is None or visitor.publication_id != pub.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _set_blocked(db, pub, visitor, False, user, request)
    return _visitor_out(visitor)


# ── visitors ───────────────────────────────────────────────────────────


#: Sent by the page framed on another site: the caller is always a visitor without an
#: account there, with its own cookie. A custom header, so another site cannot send it
#: with a visitor's cookie without passing CORS.
EMBED_HEADER = "X-KloudChat-Embed"


def _embedded(request: Request) -> bool:
    return request.headers.get(EMBED_HEADER) == "1"


async def bot_caller(request: Request, db: DbSession) -> User | None:
    """The signed-in member opening a bot, or None for a visitor without an account (and for
    an account not cleared to act: pending or suspended). Resolved by hand, never required:
    a bot open outside needs no account. Framed on another site, never a member."""
    if _embedded(request):
        return None
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    claims = decode_access_token(token)
    if not claims:
        return None
    user = await db.get(User, claims.get("sub"))
    if user is None or user.status is not UserStatus.active:
        return None
    return user


BotCaller = Annotated[User | None, Depends(bot_caller)]


def _open_to(pub: Publication, user: User | None) -> bool:
    """Who may open the page while it is open or paused: members, whatever its audience;
    people without an account once an administrator opened it outside. Its owner always."""
    if user is not None and pub.owner_id == user.id:
        return pub.status != PublicationStatus.revoked.value
    if pub.status not in (PublicationStatus.approved.value, PublicationStatus.paused.value):
        return False
    return user is not None or publishing.opens_outside(pub)


async def _bot(db: DbSession, slug: str, user: User | None,
               request: Request | None = None) -> Publication:
    # One answer for unknown, closed and not-for-you: the address gives nothing away. Framed
    # on another site, the bot must have that channel open.
    pub = (await db.exec(select(Publication).where(Publication.slug == slug[:80]))).first()
    if pub is None or not _open_to(pub, user) or (
            request is not None and _embedded(request)
            and "embed" not in publishing.channels_of(pub)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot_not_found")
    return pub


def _refused(refused: publishing.Refused) -> HTTPException:
    return HTTPException(status_code=refused.status, detail=refused.code,
                         headers={"Retry-After": str(refused.retry_after or 5)})


def _cookie_name(request: Request) -> str:
    return publishing.EMBED_VISITOR_COOKIE if _embedded(request) else publishing.VISITOR_COOKIE


def _anon_id(request: Request) -> str | None:
    return publishing.visitor_from_cookie(request.cookies.get(_cookie_name(request)))


def _ip_key(request: Request, user: User | None) -> str | None:
    """The address a visitor without an account is counted by; members are not."""
    return None if user is not None else publishing.ip_key(client_ip(request))


async def _visitor(db: DbSession, pub: Publication, user: User | None, request: Request, *,
                   create: bool = True) -> PublicationVisitor | None:
    """The caller as this bot's visitor: a member by account, anyone else by their signed
    cookie. Made on first need (counted per address for visitors without an account); with
    `create` false, None until then."""
    now = utcnow()
    if user is not None:
        row = (await db.exec(select(PublicationVisitor).where(
            PublicationVisitor.publication_id == pub.id,
            PublicationVisitor.user_id == user.id))).first()
        if row is None:
            row = PublicationVisitor(publication_id=pub.id, user_id=user.id)
            await publishing.bump(db, pub.id, publishing.day_window(now), "visitors")
    else:
        anon = _anon_id(request)
        if anon is None:
            if not create:
                return None
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="visitor_cookie_required")
        row = (await db.exec(select(PublicationVisitor).where(
            PublicationVisitor.publication_id == pub.id,
            PublicationVisitor.anon_id == anon))).first()
        if row is None:
            if not create:
                return None
            try:
                await publishing.admit_anonymous(db, pub, _ip_key(request, None) or "",
                                                 "visitors", publishing.ANON_VISITORS_PER_IP_DAY)
            except publishing.Refused as refused:
                raise _refused(refused) from None
            row = PublicationVisitor(publication_id=pub.id, anon_id=anon)
            await publishing.bump(db, pub.id, publishing.day_window(now), "visitors")
    # A new row counted itself above (its `last_seen_at` is now); an old one once a day.
    if publishing.day_window(row.last_seen_at) != publishing.day_window(now):
        await publishing.bump(db, pub.id, publishing.day_window(now), "visitors")
    row.last_seen_at = now
    db.add(row)
    return row


async def _visitor_now(db: DbSession, pub: Publication, user: User | None,
                       request: Request) -> PublicationVisitor:
    visitor = await _visitor(db, pub, user, request)
    assert visitor is not None  # create=True raises instead of returning None
    return visitor


def _ready(pub: Publication, visitor: PublicationVisitor) -> None:
    if visitor.blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="visitor_blocked")
    if pub.status != PublicationStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    if visitor.consent_version != pub.consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="consent_required")


async def _answering(db: DbSession, pub: Publication,
                     user: User | None) -> PublicationVersion:
    """The version a turn runs on. Someone without an account only ever reaches a version an
    administrator approved; anything else reads as paused."""
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    if version is None or (user is None and version.approved_at is None):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    return version


async def _colors(db: DbSession, rows: list[Publication]) -> dict[str, str]:
    """Each bot's color, from the agent copy its active version runs."""
    snaps = {v.id: v.agent_snapshot_id for v in (await db.exec(select(PublicationVersion).where(
        col(PublicationVersion.id).in_([p.active_version_id for p in rows
                                        if p.active_version_id])))).all()} if rows else {}
    colors = {a.id: a.color for a in (await db.exec(select(Agent).where(
        col(Agent.id).in_(list(snaps.values()))))).all()} if snaps else {}
    return {p.id: colors.get(snaps.get(p.active_version_id or "", ""), "#5b53e8") for p in rows}


@router.get("/bots")
async def list_bots(user: CurrentUser, db: DbSession):
    """The bots members can open — published to members or listed for everyone — and the
    caller's own. A bot open by link is listed nowhere."""
    listed = (PublicationVisibility.internal.value, PublicationVisibility.external.value)
    rows = list((await db.exec(select(Publication).where(
        (col(Publication.visibility).in_(listed)
         & (Publication.status == PublicationStatus.approved.value))
        | ((Publication.owner_id == user.id)
           & (Publication.status != PublicationStatus.revoked.value)))
        .order_by(col(Publication.published_at).desc()))).all())
    owners = {u.id: u.name for u in (await db.exec(select(User).where(
        col(User.id).in_({p.owner_id for p in rows})))).all()} if rows else {}
    colors = await _colors(db, rows)
    return [{"slug": p.slug, "title": p.title, "description": p.description,
             "ownerName": owners.get(p.owner_id, ""), "mine": p.owner_id == user.id,
             "id": p.id if p.owner_id == user.id else None, "status": p.status,
             "visibility": p.visibility,
             "needsLook": p.owner_id == user.id and _needs_look(p),
             "color": colors.get(p.id, "#5b53e8"), "publishedAt": p.published_at}
            for p in rows]


@router.get("/public/bots")
async def public_bots(db: DbSession):
    """The bots an administrator opened to everyone, for anyone: no account, no owner names."""
    rows = list((await db.exec(select(Publication).where(
        Publication.visibility == PublicationVisibility.external.value,
        Publication.status == PublicationStatus.approved.value)
        .order_by(col(Publication.published_at).desc()).limit(200))).all())
    colors = await _colors(db, rows)
    return [{"slug": p.slug, "title": p.title, "description": p.description,
             "color": colors.get(p.id, "#5b53e8"), "publishedAt": p.published_at}
            for p in rows]


def _keep_visitor_cookie(request: Request, response: Response) -> None:
    """Gives a browser without an account its signed visitor cookie, once."""
    if _anon_id(request) is not None:
        return
    _, value = publishing.new_visitor_cookie()
    if _embedded(request):
        # Framed on another site: sent there only as a third-party cookie, kept apart per
        # site (partitioned), so the frame on one site is not the visitor on another.
        # Written by hand: the cookie library takes `Partitioned` only from Python 3.14.
        response.headers.append(
            "set-cookie",
            f"{publishing.EMBED_VISITOR_COOKIE}={value}; "
            f"Max-Age={publishing.VISITOR_COOKIE_DAYS * 86400}; Path=/; HttpOnly; Secure; "
            "SameSite=None; Partitioned")
        return
    response.set_cookie(publishing.VISITOR_COOKIE, value,
                        max_age=publishing.VISITOR_COOKIE_DAYS * 86400, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/")


@router.get("/b/{slug}")
async def bot_page(slug: str, request: Request, response: Response, user: BotCaller,
                   db: DbSession):
    pub = await _bot(db, slug, user, request)
    visitor = await _visitor(db, pub, user, request, create=False)
    if user is None:
        _keep_visitor_cookie(request, response)
    owner = await db.get(User, pub.owner_id)
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    snap = await db.get(Agent, version.agent_snapshot_id) if version else None
    await db.commit()
    mine = user is not None and pub.owner_id == user.id
    # A bot open by link is for whoever holds the link, not for search engines.
    if pub.visibility != PublicationVisibility.external.value:
        response.headers["X-Robots-Tag"] = "noindex"
    return {
        "slug": pub.slug, "title": pub.title, "description": pub.description,
        # Members see who answers for the bot; people without an account see the bot.
        "ownerName": owner.name if owner and user is not None else "", "mine": mine,
        "signedIn": user is not None, "visibility": pub.visibility,
        "embedded": _embedded(request),
        "status": pub.status, "pauseReason": pub.pause_reason if mine else "",
        "branding": _branding(pub.branding), "showSources": pub.show_sources,
        "showProcess": pub.show_process, "conversationView": pub.conversation_view,
        "retentionDays": pub.retention_days, "consentVersion": pub.consent_version,
        "consented": visitor is not None and visitor.consent_version == pub.consent_version,
        "blocked": visitor is not None and visitor.blocked,
        "color": snap.color if snap else "#5b53e8",
        "maxInputChars": publishing.limits_of(pub)["maxInputChars"],
    }


@router.post("/b/{slug}/consent", status_code=status.HTTP_204_NO_CONTENT)
async def consent(slug: str, payload: ConsentIn, request: Request, user: BotCaller,
                  db: DbSession):
    """The visitor read the notice (and, with 「대화 열람」 on, agreed to it)."""
    pub = await _bot(db, slug, user, request)
    if payload.version != pub.consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="consent_outdated")
    visitor = await _visitor_now(db, pub, user, request)
    visitor.consent_version = pub.consent_version
    visitor.consented_at = utcnow()
    db.add(visitor)
    if pub.conversation_view == "on":
        _audit(db, user, "publication.consent", pub, request, version=pub.consent_version,
               visitorId=visitor.id)
    await db.commit()


async def _visited(db: DbSession, pub: Publication, user: User | None, request: Request,
                   session_id: str) -> tuple[PublicationVisitor, ChatSession]:
    """The caller's own conversation on this bot; anyone else's reads as not found."""
    visitor = await _visitor(db, pub, user, request, create=False)
    session = await db.get(ChatSession, session_id)
    if visitor is None or session is None or session.publication_id != pub.id \
            or session.visitor_id != visitor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return visitor, session


@router.get("/b/{slug}/conversations")
async def my_conversations(slug: str, request: Request, user: BotCaller, db: DbSession):
    pub = await _bot(db, slug, user, request)
    visitor = await _visitor(db, pub, user, request, create=False)
    if visitor is None:
        return []
    rows = (await db.exec(select(ChatSession).where(
        ChatSession.publication_id == pub.id, ChatSession.visitor_id == visitor.id)
        .order_by(col(ChatSession.updated_at).desc()).limit(50))).all()
    await db.commit()
    return [{"id": s.id, "title": s.title, "updatedAt": s.updated_at, "sharedAt": s.shared_at}
            for s in rows]


@router.post("/b/{slug}/conversations", status_code=status.HTTP_201_CREATED)
async def start_conversation(slug: str, request: Request, user: BotCaller, db: DbSession):
    pub = await _bot(db, slug, user, request)
    visitor = await _visitor_now(db, pub, user, request)
    _ready(pub, visitor)
    version = await _answering(db, pub, user)
    if user is None:
        try:
            await publishing.admit_anonymous(db, pub, _ip_key(request, None) or "",
                                             "conversations",
                                             publishing.ANON_CONVERSATIONS_PER_IP_DAY)
        except publishing.Refused as refused:
            raise _refused(refused) from None
    session = ChatSession(user_id=pub.owner_id, kind=SessionKind.chat, title="",
                          agent_id=version.agent_snapshot_id, model=version.model,
                          routing_mode=RoutingMode.manual, publication_id=pub.id,
                          visitor_id=visitor.id, publication_version_id=version.id,
                          viewable=pub.conversation_view == "on")
    db.add(session)
    await publishing.bump(db, pub.id, publishing.day_window(), "conversations")
    await db.commit()
    return {"id": session.id, "title": "", "updatedAt": session.updated_at, "sharedAt": None}


def _value(raw: Any) -> str:
    """An enum column's value, however the driver handed it back."""
    return str(getattr(raw, "value", raw))


def _visible_steps(steps: list | None, show: str) -> list | None:
    if show == "none" or not steps:
        return None
    if show == "summary":
        return [{k: s.get(k) for k in _STEP_KEEP if k in s} for s in steps if isinstance(s, dict)]
    return [{k: v for k, v in s.items() if k != "outputs"} for s in steps if isinstance(s, dict)]


def _visible_content(content: str, pub: Publication) -> str:
    return content if pub.show_sources else _SOURCES.sub("", content or "")


@router.get("/b/{slug}/conversations/{session_id}")
async def my_conversation(slug: str, session_id: str, request: Request, user: BotCaller,
                          db: DbSession):
    pub = await _bot(db, slug, user, request)
    _, session = await _visited(db, pub, user, request, session_id)
    messages = (await db.exec(select(Message).where(Message.session_id == session.id)
                              .order_by(col(Message.created_at), col(Message.id)))).all()
    await db.commit()
    return {
        "id": session.id, "title": session.title, "sharedAt": session.shared_at,
        "messages": [{
            "id": m.id, "role": _value(m.role), "content": _visible_content(m.content, pub),
            "steps": _visible_steps(m.steps, pub.show_process),
            "rating": _value(m.rating) if m.rating else None,
            "failure": _value(m.failure) if m.failure else None,
            "model": m.model, "createdAt": m.created_at,
        } for m in messages if m.role in (Role.user, Role.assistant)],
    }


@router.delete("/b/{slug}/conversations/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_conversation(slug: str, session_id: str, request: Request,
                                 user: BotCaller, db: DbSession):
    pub = await _bot(db, slug, user, request)
    _, session = await _visited(db, pub, user, request, session_id)
    await publishing.delete_sessions(db, [session.id])
    await db.commit()


def _visitor_stream(events, pub: Publication):
    """The turn's events as a visitor sees them: the answer and, as the owner chose, its
    working; nothing of the owner's account (credits, routes, memory, artifacts)."""

    async def relay():
        async for chunk in events:
            text = chunk if isinstance(chunk, str) else chunk.decode()
            if not text.startswith("data: "):
                yield chunk
                continue
            try:
                event = json.loads(text[6:].strip())
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "delta":
                if not pub.show_sources and str(event.get("text") or "").lstrip(
                        "\n").startswith(("### 출처", "## 출처")):
                    continue
                out = {"type": "delta", "text": event.get("text", "")}
            elif kind == "retract":
                out = {"type": "retract", "text": event.get("text", "")}
            elif kind == "step":
                steps = _visible_steps([event], pub.show_process)
                if not steps:
                    continue
                out = {"type": "step", **steps[0]}
            elif kind == "usage":
                out = {"type": "usage", "inputTokens": event.get("inputTokens", 0),
                       "outputTokens": event.get("outputTokens", 0)}
            elif kind == "privacy_route":
                out = {"type": "model", "models": list(event.get("effectiveModels") or [])}
            elif kind in ("error", "done", "title"):
                out = {k: v for k, v in event.items()
                       if k in ("type", "code", "message", "messageId", "title")}
            else:
                continue
            yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

    return relay()


def _too_many(code: str, retry_after: int | None) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": code, "retryAfter": retry_after},
                        headers={"Retry-After": str(retry_after or 5)})


@router.post("/b/{slug}/conversations/{session_id}/messages")
async def visitor_send(slug: str, session_id: str, payload: VisitorMessage, request: Request,
                       user: BotCaller, db: DbSession):
    """A visitor's question, answered by the bot's fixed version on its owner's account."""
    pub = await _bot(db, slug, user, request)
    visitor, session = await _visited(db, pub, user, request, session_id)
    _ready(pub, visitor)
    response = await run_published_turn(
        db, pub, visitor, session, content=payload.content, retry_of=payload.retry_of,
        request=request, user=user, ip_key=_ip_key(request, user),
        channel="embed" if _embedded(request) else "web")
    if isinstance(response, StreamingResponse):
        response.body_iterator = _visitor_stream(response.body_iterator, pub)
    return response


async def run_published_turn(db: DbSession, pub: Publication, visitor: PublicationVisitor,
                             session: ChatSession, *, content: str, retry_of: str | None,
                             request: Request, user: User | None, ip_key: str | None,
                             channel: str, by_key: bool = False):
    """One turn on the bot — from its page, a site framing it, or the API and MCP channels:
    limits, the version in force (approved only, without an account), a running slot, then
    the ordinary turn on the owner's account. Returns the turn's raw event stream, or the
    response that refused it."""
    from app.routers import sessions

    limits = publishing.limits_of(pub)
    if len(content) > limits["maxInputChars"]:
        raise HTTPException(status_code=422,
                            detail={"code": "too_long", "max": limits["maxInputChars"]})
    asked = int((await db.exec(select(func.count()).select_from(Message).where(
        Message.session_id == session.id, Message.role == Role.user))).one())
    if retry_of is None and asked >= limits["turnsPerConversation"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="conversation_full")
    owner = await db.get(User, pub.owner_id)
    if reason := publishing.owner_blocks(owner):
        publishing.pause(db, pub, reason)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    version = await _answering(db, pub, user)
    # The conversation runs on the version in force: a rollback reaches it at once.
    session.agent_id = version.agent_snapshot_id
    session.model = version.model
    session.publication_version_id = version.id
    db.add(session)
    try:
        await publishing.admit(db, pub, visitor.id, ip_key=ip_key, channel=channel,
                               by_key=by_key)
    except publishing.Refused as refused:
        await db.rollback()
        return _too_many(refused.code, refused.retry_after)
    await db.commit()
    try:
        policy = await governance.current_for_egress()
        cap = int(policy.published_concurrency_max or 16)
    except governance.GovernanceUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="governance_unavailable") from None
    slot = await publishing.acquire_slot(pub.id, per_bot=limits["concurrency"],
                                         instance_max=max(1, cap))
    if slot is None:
        return _too_many("bot_busy", 5)
    turn = publishing.PublishedTurn(
        publication_id=pub.id, version_id=version.id, visitor_id=visitor.id,
        model_id=version.model, question=content, slot_id=slot,
        first_turn=asked == 0)
    token = sessions._PUBLISHED.set(turn)
    try:
        response = await sessions.send_message(
            session.id, SendMessage(content=content, retry_of=retry_of),
            request, owner, db)
    except HTTPException as exc:
        await turn.release()
        if exc.detail == "published_model_unavailable":
            publishing.pause(db, pub, "model")
            await db.commit()
        raise
    except BaseException:
        await turn.release()
        raise
    finally:
        sessions._PUBLISHED.reset(token)
    if not isinstance(response, StreamingResponse):
        await turn.release()
        if response.status_code == status.HTTP_409_CONFLICT:
            # A strict-local route the version named is gone: closed until the owner re-publishes.
            publishing.pause(db, pub, "model")
            await db.commit()
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="published_model_unavailable")
    return response


@router.post("/b/{slug}/conversations/{session_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def visitor_stop(slug: str, session_id: str, request: Request, user: BotCaller,
                       db: DbSession):
    from app.routers import sessions

    pub = await _bot(db, slug, user, request)
    _, session = await _visited(db, pub, user, request, session_id)
    await db.commit()
    sessions.fire_stop_locally(session.id)
    await stop_signal.broadcast(session.id)
    await sessions._release_turn(session.id, token="")


@router.patch("/b/{slug}/messages/{message_id}/rating")
async def visitor_rate(slug: str, message_id: str, payload: VisitorRating, request: Request,
                       user: BotCaller, db: DbSession):
    """👍👎 on one answer; the owner sees the counts, not the answer."""
    pub = await _bot(db, slug, user, request)
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _visited(db, pub, user, request, message.session_id)
    if message.role is not Role.assistant:
        raise HTTPException(status_code=422, detail="not_an_answer")
    day = publishing.day_window()
    before = message.rating
    if before:
        await publishing.bump(db, pub.id, day, _value(before), -1)
    if payload.rating is not None:
        await publishing.bump(db, pub.id, day, payload.rating.value, 1)
    message.rating = payload.rating
    db.add(message)
    await db.commit()
    return {"id": message.id, "rating": payload.rating.value if payload.rating else None}


@router.post("/b/{slug}/conversations/{session_id}/share")
async def visitor_share(slug: str, session_id: str, request: Request, user: BotCaller,
                        db: DbSession):
    """「만든 사람에게 보내기」: this one conversation becomes readable to the bot's maker,
    personal details masked."""
    pub = await _bot(db, slug, user, request)
    visitor, session = await _visited(db, pub, user, request, session_id)
    if session.shared_at is None:
        session.shared_at = utcnow()
        db.add(session)
        await publishing.bump(db, pub.id, publishing.day_window(), "shared")
        _audit(db, user, "publication.shared", pub, request, sessionId=session.id,
               visitorId=visitor.id)
    await db.commit()
    return {"id": session.id, "sharedAt": session.shared_at}


@router.delete("/b/{slug}/conversations/{session_id}/share")
async def visitor_unshare(slug: str, session_id: str, request: Request, user: BotCaller,
                          db: DbSession):
    pub = await _bot(db, slug, user, request)
    _, session = await _visited(db, pub, user, request, session_id)
    session.shared_at = None
    db.add(session)
    await db.commit()
    return {"id": session.id, "sharedAt": None}


# ── administrators ─────────────────────────────────────────────────────


@router.get("/admin/publications")
async def admin_list(admin: AdminUser, db: DbSession,
                     review: str = Query("", pattern="^(|pending)$")):
    """Every bot, requests waiting for review first; `review=pending` for the queue only."""
    query = select(Publication)
    if review:
        query = query.where(Publication.review_state == ReviewState.pending.value)
    rows = sorted((await db.exec(query.order_by(col(Publication.updated_at).desc()))).all(),
                  key=lambda p: p.review_state != ReviewState.pending.value)
    owners = {u.id: u for u in (await db.exec(select(User).where(
        col(User.id).in_({p.owner_id for p in rows})))).all()} if rows else {}
    counts = dict((await db.exec(select(ChatSession.publication_id, func.count()).where(
        col(ChatSession.publication_id).in_([p.id for p in rows])).group_by(
        col(ChatSession.publication_id)))).all()) if rows else {}
    return [{**_pub_out(p, owner_name=owners[p.owner_id].name if p.owner_id in owners else ""),
             "ownerEmail": owners[p.owner_id].email if p.owner_id in owners else "",
             "conversations": int(counts.get(p.id, 0))} for p in rows]


async def _admin_pub(db: DbSession, publication_id: str) -> Publication:
    pub = await db.get(Publication, publication_id)
    if pub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return pub


@router.get("/admin/publications/{publication_id}/review")
async def admin_review(publication_id: str, admin: AdminUser, db: DbSession):
    """What an administrator decides on: the version that would answer people without an
    account — its instructions, contact line, checks, tools, model and material, with the
    personal details the material holds — the bot's settings and limits, and its owner."""
    pub = await _admin_pub(db, publication_id)
    candidate_id = pub.pending_version_id or pub.active_version_id
    version = await db.get(PublicationVersion, candidate_id) if candidate_id else None
    snap = await db.get(Agent, version.agent_snapshot_id) if version else None
    files = list((await db.exec(select(StoredFile).where(
        StoredFile.agent_id == snap.id).order_by(col(StoredFile.created_at)))).all()) \
        if snap else []
    owner = await db.get(User, pub.owner_id)
    history = (await db.exec(select(AuditEvent).where(
        AuditEvent.target == pub.id, col(AuditEvent.action).in_([
            "publication.published", "publication.requested", "publication.reviewed",
            "publication.withdrawn", "publication.narrowed", "publication.version",
            "publication.request_withdrawn", "publication.admin_status",
            "publication.channels_requested", "publication.channels_closed"]))
        .order_by(col(AuditEvent.at).desc()).limit(30))).all()
    knowledge = []
    for row in files:
        found = governance.findings({"knowledge": row.text}, scope="egress")
        knowledge.append({"name": row.name, "chars": len(row.text or ""),
                          "findings": [{"category": f.category, "count": f.count}
                                       for f in found]})
    return {
        "publication": _pub_out(pub, versions=await _versions(db, pub),
                                owner_name=owner.name if owner else ""),
        "owner": {"name": owner.name if owner else "", "email": owner.email if owner else ""},
        "version": _version_out(version, pub.active_version_id, pub.pending_version_id)
        if version else None,
        "agent": {
            "name": snap.name, "description": snap.description,
            "instructions": snap.system_prompt, "contact": snap.contact or "",
            "guide": snap.guide or "", "starters": list(snap.starters or []),
            "tools": list(snap.tools or []), "checks": list(snap.check_skills or []),
            "model": snap.model,
        } if snap else None,
        "knowledge": knowledge,
        "history": [{"action": e.action, "at": e.at, "actorId": e.actor_id,
                     "metadata": {k: v for k, v in (e.event_metadata or {}).items()
                                  if k in ("visibility", "version", "decision", "reason",
                                           "status", "previous", "pending", "channels")}}
                    for e in history],
    }


@router.post("/admin/publications/{publication_id}/review")
async def admin_decide(publication_id: str, payload: ReviewIn, request: Request,
                       admin: AdminUser, db: DbSession):
    """Approves a request — the audience asked for and the version waiting, together — or
    refuses it with a reason the owner reads. The bot keeps what it had when refused."""
    pub = await _admin_pub(db, publication_id)
    if pub.review_state != ReviewState.pending.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="nothing_to_review")
    now = utcnow()
    if payload.decision == "approve":
        candidate_id = pub.pending_version_id or pub.active_version_id
        version = await db.get(PublicationVersion, candidate_id) if candidate_id else None
        if version is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_version")
        version.approved_at, version.approved_by = now, admin.id
        db.add(version)
        pub.active_version_id, pub.pending_version_id = version.id, None
        if pub.requested_visibility:
            pub.visibility = pub.requested_visibility
        pub.requested_visibility = ""
        # The channels asked for open with the audience; what it cannot carry stays shut.
        if pub.requested_channels is not None:
            pub.channels = pub.requested_channels
        pub.channels = publishing.fit_channels(pub.channels, pub.visibility)
        pub.requested_channels = None
        pub.review_state = ReviewState.none.value
        pub.review_note = " ".join(payload.reason.split())[:300]
        detail = {"version": version.number, "visibility": pub.visibility,
                  "channels": sorted(publishing.channels_of(pub))}
    else:
        reason = _reason(payload.reason)
        pub.review_state = ReviewState.rejected.value
        pub.review_note = reason
        pub.pending_version_id = None
        detail = {"visibility": pub.requested_visibility, "reason": reason,
                  "channels": sorted(pub.requested_channels or {})}
        pub.requested_channels = None
    pub.reviewed_at, pub.reviewed_by = now, admin.id
    pub.updated_at = now
    publishing.flag_owner(pub)
    db.add(pub)
    _audit(db, admin, "publication.reviewed", pub, request, severity="warn",
           decision=payload.decision, **detail)
    await db.commit()
    return _pub_out(pub)


@router.post("/admin/publications/{publication_id}/withdraw")
async def admin_withdraw(publication_id: str, payload: WithdrawIn, request: Request,
                         admin: AdminUser, db: DbSession):
    """Closes the outside audience at once: members only again, with a reason the owner
    reads. Visitors without an account lose the bot with their next request."""
    pub = await _admin_pub(db, publication_id)
    reason = _reason(payload.reason)
    previous = pub.visibility
    pub.visibility = PublicationVisibility.internal.value
    pub.requested_visibility = ""
    # Every channel goes with the outside audience: frames, the API and MCP stop at once.
    pub.channels = None
    pub.requested_channels = None
    pub.review_state = ReviewState.none.value
    pub.review_note = reason
    if pub.pending_version_id:
        pub.pending_version_id = None
    pub.reviewed_at, pub.reviewed_by = utcnow(), admin.id
    pub.updated_at = utcnow()
    publishing.flag_owner(pub)
    db.add(pub)
    _audit(db, admin, "publication.withdrawn", pub, request, severity="warn",
           previous=previous, reason=reason)
    await db.commit()
    return _pub_out(pub)


@router.post("/admin/publications/{publication_id}/status")
async def admin_status(publication_id: str, payload: AdminStatusIn, request: Request,
                       admin: AdminUser, db: DbSession):
    pub = await _admin_pub(db, publication_id)
    if payload.status == "paused":
        publishing.pause(db, pub, "admin", actor_id=admin.id)
    elif payload.status == "revoked":
        pub.status, pub.pause_reason = PublicationStatus.revoked.value, "admin"
    else:
        pub.status, pub.pause_reason = PublicationStatus.approved.value, ""
    pub.review_note = " ".join(payload.reason.split())[:300]
    pub.updated_at = utcnow()
    publishing.flag_owner(pub)
    db.add(pub)
    _audit(db, admin, "publication.admin_status", pub, request, severity="warn",
           status=payload.status, reason=payload.reason)
    await db.commit()
    return _pub_out(pub)


def _reason(reason: str) -> str:
    reason = " ".join((reason or "").split())[:300]
    if len(reason) < publishing.ADMIN_REASON_MIN:
        raise HTTPException(status_code=422,
                            detail="reason_required")
    return reason


@router.get("/admin/publications/{publication_id}/conversations")
async def admin_conversations(publication_id: str, request: Request, admin: AdminUser,
                              db: DbSession, reason: str = Query("", max_length=300)):
    """Every conversation of a bot, for an abuse review only: the reason is recorded."""
    why = _reason(reason)
    pub = await db.get(Publication, publication_id)
    if pub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    rows = (await db.exec(select(ChatSession).where(ChatSession.publication_id == pub.id)
                          .order_by(col(ChatSession.updated_at).desc()).limit(200))).all()
    counts = await _turn_counts(db, [s.id for s in rows])
    visitors = await _visitors_of(db, list(rows))
    _audit(db, admin, "publication.admin_list", pub, request, severity="warn", reason=why)
    await db.commit()
    return [_conversation_row(s, int(counts.get(s.id, 0)), visitors.get(s.visitor_id or ""))
            for s in rows]


@router.get("/admin/publications/{publication_id}/conversations/{session_id}")
async def admin_conversation(publication_id: str, session_id: str, request: Request,
                             admin: AdminUser, db: DbSession,
                             reason: str = Query("", max_length=300)):
    why = _reason(reason)
    pub = await db.get(Publication, publication_id)
    session = await db.get(ChatSession, session_id)
    if pub is None or session is None or session.publication_id != pub.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    messages = (await db.exec(select(Message).where(Message.session_id == session.id)
                              .order_by(col(Message.created_at), col(Message.id)))).all()
    _audit(db, admin, "publication.admin_read", pub, request, severity="warn",
           sessionId=session.id, reason=why)
    log.info("administrator read a bot conversation (publication %s)", logs.safe(pub.id))
    await db.commit()
    visitor = await db.get(PublicationVisitor, session.visitor_id) if session.visitor_id \
        else None
    return {**_conversation_row(session, sum(1 for m in messages if m.role is Role.user),
                                visitor),
            "messages": publishing.masked_transcript(list(messages))}


__all__ = ["EMBED_HEADER", "bot_caller", "router", "run_published_turn"]

