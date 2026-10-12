"""Published agents (공개 챗봇): owner screens, visitor pages, admin review of outside audiences."""

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
from app.services import governance, publishing, skill_packages, stop_signal
from app.services import models as model_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["publications"])

_STEP_KEEP = ("id", "label", "status", "category")
_SOURCES = re.compile(r"\n{1,3}#{2,3} ?출처[ \t]{0,4}\n[\s\S]{0,8000}$")


# ── shared ──


def _branding(raw: dict | None) -> dict[str, Any]:
    raw = raw or {}
    prompts = [" ".join(str(p).split())[:200] for p in raw.get("suggestedPrompts") or []
               if str(p).strip()][:4]
    return {"greeting": str(raw.get("greeting") or "")[:400],
            "suggestedPrompts": prompts,
            "placeholder": str(raw.get("placeholder") or "")[:120]}


def _apply(pub: Publication, payload: PublicationSettings) -> None:
    """Owner settings; notice changes bump consent version."""
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
    if "allow_copy" in fields and payload.allow_copy is not None:
        pub.allow_copy = payload.allow_copy
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
        # visitors re-read the notice before next question
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
    """Bot activity since owner's last visit."""
    if pub.attention_at is None:
        return False
    if pub.owner_seen_at is None:
        return True
    # UTC; driver may drop tzinfo
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
        "allowCopy": pub.allow_copy,
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


async def _named_out(db: DbSession, pub: Publication) -> dict[str, Any]:
    """`_pub_out` plus agent and owner names."""
    agent = await db.get(Agent, pub.agent_id) if pub.agent_id else None
    owner = await db.get(User, pub.owner_id)
    return _pub_out(pub, agent_name=agent.name if agent is not None else "",
                    owner_name=owner.name if owner is not None else "")


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
    """Bot and all its data: conversations, versions, copies, counts."""
    ids = list((await db.exec(select(ChatSession.id).where(
        ChatSession.publication_id == pub.id))).all())
    await publishing.delete_sessions(db, ids)
    await publishing.drop_versions(db, await _versions(db, pub))
    for model in (PublicationVersion, PublicationVisitor, PublicationQuestion, PublicationSlot,
                  PublicationCounter, PublicationKey):
        await db.exec(delete(model).where(model.publication_id == pub.id))
    await db.delete(pub)


# ── owner ──


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
    """Badges: own bots with news; pending reviews for admins."""
    mine = (await db.exec(select(Publication).where(
        Publication.owner_id == user.id, col(Publication.attention_at).is_not(None)))).all()
    reviews = 0
    if user.role is UserRole.admin:
        reviews = int((await db.exec(select(func.count()).select_from(Publication).where(
            Publication.review_state == ReviewState.pending.value))).one())
    return {"bots": sum(1 for p in mine if _needs_look(p)), "reviews": reviews}


def _request(pub: Publication, visibility: str) -> None:
    """Wider-audience request; caller audits and commits."""
    pub.requested_visibility = visibility
    pub.review_state = ReviewState.pending.value
    pub.review_note = ""
    pub.updated_at = utcnow()


def _require_cap(pub: Publication) -> None:
    if publishing.credits_cap(pub) is None:
        raise HTTPException(status_code=422, detail="credits_cap_required")


@router.post("/publications/preflight")
async def preflight(payload: PreflightIn, user: CurrentUser, db: DbSession):
    """Publish readiness, internal model, omissions, knowledge PII."""
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
    """Publish to members as v1; wider audience requested at once."""
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
    return _pub_out(pub, versions=await _versions(db, pub), agent_name=agent.name,
                    owner_name=user.name)


@router.get("/publications/{publication_id}")
async def get_publication(publication_id: str, user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    agent = await db.get(Agent, pub.agent_id) if pub.agent_id else None
    out = _pub_out(pub, versions=await _versions(db, pub),
                   agent_name=agent.name if agent is not None else "", owner_name=user.name)
    # badge cleared
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
        # outside (open or requested): daily credit cap required
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
    """Permanent close; conversations, versions, stats deleted."""
    pub = await _mine(db, user, publication_id)
    _audit(db, user, "publication.deleted", pub, request)
    await _remove(db, pub)
    await db.commit()


@router.post("/publications/{publication_id}/scope")
async def set_scope(publication_id: str, payload: ScopeIn, request: Request, user: CurrentUser,
                    db: DbSession):
    """Audience: narrower applies now; wider needs admin review and daily cap."""
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
        # channels the narrower audience can't carry close
        pub.channels = publishing.fit_channels(pub.channels, target)
        pub.requested_channels = publishing.fit_channels(pub.requested_channels, target)
        if target == PublicationVisibility.internal.value and pub.pending_version_id:
            # members only: pending version needs no review
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
    """Request withdrawal: wider audience and pending version."""
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
    """Next version from current agent; outside bots await review."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    agent = await _own_agent(db, user, pub.agent_id)
    catalogue, safe = await _model_context(user)
    checked = await publishing.preflight(db, user, agent, catalogue, safe)
    if not checked["ok"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "cannot_publish", "problems": checked["problems"]})
    if await publishing.same_as_latest(db, pub, agent, checked["model"]["id"]):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_change")
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
    """Rollback/forward; unapproved version on outside bot awaits review."""
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


# ── channels, keys ──


@router.post("/publications/{publication_id}/channels")
async def set_channels(publication_id: str, payload: ChannelsIn, request: Request,
                       user: CurrentUser, db: DbSession):
    """Channel set: closing immediate; opening needs admin review and daily cap."""
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
    # in-force or requested audience must carry the channel
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
    """Bot API/MCP key, returned once; hash stored."""
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
    return {**_key_out(row), "key": key}


@router.delete("/publications/{publication_id}/keys/{key_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(publication_id: str, key_id: str, request: Request, user: CurrentUser,
                     db: DbSession):
    """Immediate stop; revoked row kept for audit."""
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
    """Admin conversation reads and reasons, visible to owner."""
    pub = await _mine(db, user, publication_id)
    rows = (await db.exec(select(AuditEvent).where(
        AuditEvent.target == pub.id,
        col(AuditEvent.action).in_(["publication.admin_list", "publication.admin_read"]))
        .order_by(col(AuditEvent.at).desc()).limit(100))).all()
    out: list[dict[str, Any]] = []
    for e in rows:
        kind = "list" if e.action == "publication.admin_list" else "read"
        reason = str((e.event_metadata or {}).get("reason") or "")
        last = out[-1] if out else None
        # collapse same-minute reads into one line
        if last is not None and last["kind"] == kind and last["reason"] == reason \
                and abs((last["at"] - e.at).total_seconds()) < 60:
            last["count"] += 1
            continue
        out.append({"at": e.at, "kind": kind, "reason": reason, "count": 1})
    return out


def _days(days: int) -> list[str]:
    now = utcnow()
    return [publishing.day_window(now - timedelta(days=i)) for i in range(days - 1, -1, -1)]


@router.get("/publications/{publication_id}/stats")
async def stats(publication_id: str, user: CurrentUser, db: DbSession,
                days: int = Query(30, ge=1, le=90)):
    """Usage, topics, unanswered (masked), 👍👎; never conversations."""
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
        # by origin: page, framing site, API, MCP
        "channels": by_channel,
        "topics": [{"word": w, "count": c}
                   for w, c in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))[:15]],
        "unanswered": [{"text": q.text, "at": q.at} for q in questions],
    }


def _visitor_out(visitor: PublicationVisitor | None) -> dict[str, Any] | None:
    """Owner-visible visitor: label, signed-in, blocked; never identity."""
    if visitor is None:
        return None
    return {"id": visitor.id, "label": visitor.id[:6].upper(),
            "member": visitor.user_id is not None, "blocked": visitor.blocked,
            "blockedAt": visitor.blocked_at,
            # owner's key (API/MCP), not a person
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
    """Owner-readable conversations: sent ones, plus post-consent ones with 「대화 열람」."""
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
    """Block the visitor of an owner-readable conversation."""
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
    """Owner's blocked visitors."""
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


# ── visitors ──


#: framed page marker (always anonymous); custom header → CORS blocks cross-site cookie use
EMBED_HEADER = "X-KloudChat-Embed"


def _embedded(request: Request) -> bool:
    return request.headers.get(EMBED_HEADER) == "1"


async def bot_caller(request: Request, db: DbSession) -> User | None:
    """Signed-in active member or None; optional auth; framed → never a member."""
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
    """Open to: members always, anonymous once admin-opened outside, owner always."""
    if user is not None and pub.owner_id == user.id:
        return pub.status != PublicationStatus.revoked.value
    if pub.status not in (PublicationStatus.approved.value, PublicationStatus.paused.value):
        return False
    return user is not None or publishing.opens_outside(pub)


async def _bot(db: DbSession, slug: str, user: User | None,
               request: Request | None = None) -> Publication:
    # same answer for unknown/closed/not-for-you; framed needs embed channel
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
    """Address key for anonymous visitors; members None."""
    return None if user is not None else publishing.ip_key(client_ip(request))


async def _visitor(db: DbSession, pub: Publication, user: User | None, request: Request, *,
                   create: bool = True) -> PublicationVisitor | None:
    """Caller as bot visitor (account or signed cookie); lazy create; None if `create` false."""
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
    # new row already counted; old once a day
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
    """Turn version; anonymous only reach approved, else paused."""
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    if version is None or (user is None and version.approved_at is None):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    return version


async def _colors(db: DbSession, rows: list[Publication]) -> dict[str, str]:
    """Bot colors from active version's agent copy."""
    snaps = {v.id: v.agent_snapshot_id for v in (await db.exec(select(PublicationVersion).where(
        col(PublicationVersion.id).in_([p.active_version_id for p in rows
                                        if p.active_version_id])))).all()} if rows else {}
    colors = {a.id: a.color for a in (await db.exec(select(Agent).where(
        col(Agent.id).in_(list(snaps.values()))))).all()} if snaps else {}
    return {p.id: colors.get(snaps.get(p.active_version_id or "", ""), "#5b53e8") for p in rows}


_LISTED = (PublicationVisibility.internal.value, PublicationVisibility.external.value)


@router.get("/bots")
async def list_bots(user: CurrentUser, db: DbSession):
    """Member-openable bots plus caller's own; link-only bots unlisted."""
    rows = list((await db.exec(select(Publication).where(
        (col(Publication.visibility).in_(_LISTED)
         & (Publication.status == PublicationStatus.approved.value))
        | ((Publication.owner_id == user.id)
           & (Publication.status != PublicationStatus.revoked.value)))
        .order_by(col(Publication.published_at).desc()))).all())
    owners = {u.id: u.name for u in (await db.exec(select(User).where(
        col(User.id).in_({p.owner_id for p in rows})))).all()} if rows else {}
    colors = await _colors(db, rows)
    sources = [p.agent_id for p in rows if p.agent_id]
    # Source agents also shared in the org: the app lists those once, as agents.
    org_shared = set((await db.exec(select(Agent.id).where(
        col(Agent.id).in_(sources), Agent.visibility == "org",
        col(Agent.retired).is_(False)))).all()) if sources else set()
    sealed = set((await db.exec(select(Agent.id).where(
        col(Agent.id).in_(sources), Agent.share_mode == "sealed"))).all()) if sources else set()
    copied = set((await db.exec(select(Agent.origin_id).where(
        Agent.owner_id == user.id, col(Agent.origin_id).in_(sources)))).all()) if sources else set()
    return [{"slug": p.slug, "title": p.title, "description": p.description,
             "ownerName": owners.get(p.owner_id, ""), "mine": p.owner_id == user.id,
             "id": p.id if p.owner_id == user.id else None, "status": p.status,
             "visibility": p.visibility,
             "needsLook": p.owner_id == user.id and _needs_look(p),
             "color": colors.get(p.id, "#5b53e8"), "publishedAt": p.published_at,
             "sharedAgentId": p.agent_id if p.agent_id in org_shared else None,
             "copied": p.agent_id in copied,
             "copyable": p.owner_id != user.id and p.allow_copy and p.agent_id not in sealed}
            for p in rows]


async def _free_slug(db: DbSession, owner_id: str, base: str) -> str:
    """Owner-unique agent slug: `base`, else `base-2`, `base-3`, ..."""
    taken = set((await db.exec(select(Agent.slug).where(Agent.owner_id == owner_id))).all())
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


@router.post("/bots/{slug}/copy", status_code=status.HTTP_201_CREATED)
async def copy_bot(slug: str, user: CurrentUser, db: DbSession):
    """A member's own agent from a listed bot's answering version; no knowledge, no skills."""
    from app.routers import workspace
    from app.schemas.workspace import AgentOut

    pub = (await db.exec(select(Publication).where(Publication.slug == slug))).first()
    if pub is None or pub.visibility not in _LISTED \
            or pub.status != PublicationStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if pub.owner_id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_yours")
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    snap = await db.get(Agent, version.agent_snapshot_id) if version else None
    if snap is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    source = await db.get(Agent, pub.agent_id) if pub.agent_id else None
    # Locked unless the publisher allows copies; the agent's 「프롬프트 비공개」 still holds.
    if not pub.allow_copy or (source is not None and source.share_mode == "sealed"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="copy_closed")
    origin_id = pub.agent_id or snap.id
    held = (await db.exec(select(Agent).where(
        Agent.owner_id == user.id, Agent.origin_id == origin_id))).first()
    if held is None:
        held = Agent(
            owner_id=user.id,
            name=pub.title or snap.name,
            slug=await _free_slug(db, user.id, workspace._slug(pub.title or snap.name)),
            description=pub.description or snap.description,
            model=snap.model,
            system_prompt=snap.system_prompt,
            guide=snap.guide,
            contact=snap.contact,
            starters=list(snap.starters or []),
            tools=None if snap.tools is None else list(snap.tools),
            kinds=["chat"],
            surface="chat",
            base_key=snap.base_key,
            temperature=snap.temperature,
            color=snap.color,
            check_skills=list(snap.check_skills) if snap.check_skills is not None else None,
            check_options=dict(snap.check_options) if snap.check_options else None,
            process_settings=dict(snap.process_settings)
            if snap.process_settings is not None else None,
            enabled=True,
            origin_id=origin_id,
        )
        db.add(held)
        await db.commit()
        await db.refresh(held)
    return AgentOut.of(held, has_knowledge=False)


@router.get("/public/bots")
async def public_bots(db: DbSession):
    """Admin-opened public bots; no account, no owner names."""
    rows = list((await db.exec(select(Publication).where(
        Publication.visibility == PublicationVisibility.external.value,
        Publication.status == PublicationStatus.approved.value)
        .order_by(col(Publication.published_at).desc()).limit(200))).all())
    colors = await _colors(db, rows)
    return [{"slug": p.slug, "title": p.title, "description": p.description,
             "color": colors.get(p.id, "#5b53e8"), "publishedAt": p.published_at}
            for p in rows]


def _keep_visitor_cookie(request: Request, response: Response) -> None:
    """Signed visitor cookie for anonymous browsers, once."""
    if _anon_id(request) is not None:
        return
    _, value = publishing.new_visitor_cookie()
    if _embedded(request):
        # framed: partitioned third-party cookie per site; by hand (`Partitioned` needs Python 3.14)
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
    # link bots not for search engines
    if pub.visibility != PublicationVisibility.external.value:
        response.headers["X-Robots-Tag"] = "noindex"
    return {
        "slug": pub.slug, "title": pub.title, "description": pub.description,
        # owner name for members only
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
    """Notice read (and agreed with 「대화 열람」)."""
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
    """Caller's own conversation; others 404."""
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
    """Enum column value, any driver shape."""
    return str(getattr(raw, "value", raw))


def _happened(step: Any) -> bool:
    """Step ran; inapplicable stages (「해당 없음」) are server working, not answer steps."""
    if not isinstance(step, dict):
        return False
    stage = step.get("stage")
    return not (isinstance(stage, dict) and stage.get("status") == "skipped")


def _visible_steps(steps: list | None, show: str) -> list | None:
    if show == "none" or not steps:
        return None
    steps = [s for s in steps if _happened(s)]
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
    """Visitor view of turn events: answer and owner-chosen working; no owner account data."""

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
    """Visitor question on bot's fixed version, owner's account."""
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
                             channel: str, by_key: bool = False, question: str | None = None):
    """One bot turn (page/frame/API/MCP): limits, version, slot, owner-account turn."""
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
    # version in force: rollback reaches it at once
    session.agent_id = version.agent_snapshot_id
    session.model = version.model
    session.publication_version_id = version.id
    db.add(session)
    if session.kind is SessionKind.chat and sessions.turn_running_here(session.id):
        # refused before counting: busy session spends no limits
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session_busy")
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
        model_id=version.model, question=question or content, slot_id=slot,
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
            # named strict-local route gone: closed until republish
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
    """👍👎 on one answer; owner sees counts only."""
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
    """「만든 사람에게 보내기」: conversation shared to maker, PII masked."""
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


# ── admin ──


@router.get("/admin/publications")
async def admin_list(admin: AdminUser, db: DbSession,
                     review: str = Query("", pattern="^(|pending)$")):
    """All bots, pending first; `review=pending` for queue only."""
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


def _request_kind(pub: Publication) -> str:
    """Open review kind: `visibility`, `channels` or `version`."""
    if pub.requested_visibility:
        return "visibility"
    if pub.requested_channels is not None and not pub.pending_version_id:
        return "channels"
    return "version"


def _check_names(keys: list[str]) -> list[str]:
    """Check skills by display name."""
    out = []
    for key in keys:
        package = skill_packages.package_for(key)
        out.append(package.name if package is not None and package.name else key)
    return out


async def _snapshot_facts(db: DbSession, version: PublicationVersion | None) -> dict[str, Any]:
    """Version facts for comparison."""
    snap = await db.get(Agent, version.agent_snapshot_id) if version else None
    if snap is None:
        return {}
    files = (await db.exec(select(StoredFile).where(StoredFile.agent_id == snap.id)
                           .order_by(col(StoredFile.created_at)))).all()
    return {
        "instructions": snap.system_prompt or "", "contact": snap.contact or "",
        "model": snap.model or "", "tools": sorted(snap.tools or []),
        "checks": _check_names(list(snap.check_skills or [])),
        "knowledge": [f"{f.name} · {len(f.text or ''):,}자" for f in files],
    }


async def _version_changes(db: DbSession, pub: Publication) -> list[dict[str, Any]]:
    """Pending vs active version diff by field."""
    if not pub.pending_version_id or not pub.active_version_id \
            or pub.pending_version_id == pub.active_version_id:
        return []
    before = await _snapshot_facts(db, await db.get(PublicationVersion, pub.active_version_id))
    after = await _snapshot_facts(db, await db.get(PublicationVersion, pub.pending_version_id))
    return [{"field": key, "before": before.get(key), "after": after.get(key)}
            for key in ("instructions", "contact", "model", "tools", "checks", "knowledge")
            if before and after and before.get(key) != after.get(key)]


#: chars per material file shown to admin (PII masked)
_PREVIEW_CHARS = 1500


@router.get("/admin/publications/{publication_id}/review")
async def admin_review(publication_id: str, admin: AdminUser, db: DbSession):
    """Review payload: anonymous-facing version, material PII, settings, limits, owner."""
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
                                       for f in found],
                          "preview": governance.mask((row.text or "")[:_PREVIEW_CHARS],
                                                     scope="egress")[0]})
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
            "checkNames": _check_names(list(snap.check_skills or [])),
            "model": snap.model,
        } if snap else None,
        "request": _request_kind(pub) if pub.review_state == ReviewState.pending.value else None,
        "changes": await _version_changes(db, pub),
        "knowledge": knowledge,
        "history": [{"action": e.action, "at": e.at, "actorId": e.actor_id,
                     "metadata": {k: v for k, v in (e.event_metadata or {}).items()
                                  if k in ("visibility", "version", "decision", "reason",
                                           "status", "previous", "pending", "channels",
                                           "asked")}}
                    for e in history],
    }


@router.post("/admin/publications/{publication_id}/review")
async def admin_decide(publication_id: str, payload: ReviewIn, request: Request,
                       admin: AdminUser, db: DbSession):
    """Approve audience+version together, or refuse with owner-visible reason."""
    pub = await _admin_pub(db, publication_id)
    if pub.review_state != ReviewState.pending.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="nothing_to_review")
    now = utcnow()
    # recorded request kind
    asked = _request_kind(pub)
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
        # requested channels open with audience; uncarried stay shut
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
           decision=payload.decision, asked=asked, **detail)
    await db.commit()
    return await _named_out(db, pub)


@router.post("/admin/publications/{publication_id}/withdraw")
async def admin_withdraw(publication_id: str, payload: WithdrawIn, request: Request,
                         admin: AdminUser, db: DbSession):
    """Close outside audience now (members only), with owner-visible reason."""
    pub = await _admin_pub(db, publication_id)
    reason = _reason(payload.reason)
    previous = pub.visibility
    pub.visibility = PublicationVisibility.internal.value
    pub.requested_visibility = ""
    # all channels close with outside audience
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
    return await _named_out(db, pub)


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
    return await _named_out(db, pub)


def _reason(reason: str) -> str:
    reason = " ".join((reason or "").split())[:300]
    # real words required, not key mashing
    if len(reason) < publishing.ADMIN_REASON_MIN \
            or len({ch for ch in reason if ch.isalpha()}) < 2:
        raise HTTPException(status_code=422,
                            detail="reason_required")
    return reason


@router.get("/admin/publications/{publication_id}/conversations")
async def admin_conversations(publication_id: str, request: Request, admin: AdminUser,
                              db: DbSession, reason: str = Query("", max_length=300)):
    """All bot conversations for abuse review; reason recorded."""
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

