"""Published agents (공개 챗봇): the owner's screens, the members' bot page, and the
administrator's review. Phase 1 opens bots to signed-in members of this instance only.

- `/publications…` — the owner: preflight, publish, versions and rollback, settings, pause,
  statistics, and the conversations visitors sent them (or, with 「대화 열람」 on, those started
  after the visitor's consent), personal details masked.
- `/bots`, `/b/{slug}…` — members: the list of bots, a bot's page, the notice and consent,
  their own conversations with it, 👍👎 and 「만든 사람에게 보내기」. A visitor's turn is the
  ordinary turn (`sessions.send_message`) run on the owner's account under the bot's fixed
  version (`services.publishing`).
- `/admin/publications…` — administrators: every bot, pausing or closing one, and reading a
  conversation for an abuse review, with a reason that is recorded.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func
from sqlmodel import col, delete, select

from app.core import logs
from app.core.deps import AdminUser, CurrentUser, DbSession, client_ip
from app.models.chat import ChatSession, Message, Role, RoutingMode, SessionKind
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
from app.models.user import AuditEvent, User, utcnow
from app.models.workspace import Agent
from app.schemas.chat import SendMessage
from app.schemas.publishing import (
    AdminStatusIn,
    ConsentIn,
    PreflightIn,
    PublicationPatch,
    PublicationSettings,
    PublishIn,
    VersionIn,
    VisitorMessage,
    VisitorRating,
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


def _version_out(v: PublicationVersion, active: str | None) -> dict[str, Any]:
    return {"id": v.id, "number": v.number, "model": v.model, "note": v.note,
            "summary": v.summary or {}, "active": v.id == active, "createdAt": v.created_at}


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
        "createdAt": pub.created_at, "updatedAt": pub.updated_at,
        "publishedAt": pub.published_at,
    }
    if versions is not None:
        out["versions"] = [_version_out(v, pub.active_version_id) for v in versions]
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
                  PublicationCounter):
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
    """Publishes the agent to members as version 1."""
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
    db.add(pub)
    await db.flush()
    version = await publishing.make_version(db, user, pub, agent, checked["model"]["id"],
                                            payload.note)
    _audit(db, user, "publication.published", pub, request, version=version.number,
           model=version.model)
    await db.commit()
    return _pub_out(pub, versions=await _versions(db, pub), agent_name=agent.name)


@router.get("/publications/{publication_id}")
async def get_publication(publication_id: str, user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    agent = await db.get(Agent, pub.agent_id) if pub.agent_id else None
    return _pub_out(pub, versions=await _versions(db, pub),
                    agent_name=agent.name if agent is not None else "")


@router.patch("/publications/{publication_id}")
async def patch_publication(publication_id: str, payload: PublicationPatch, request: Request,
                            user: CurrentUser, db: DbSession):
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    _apply(pub, payload)
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


@router.post("/publications/{publication_id}/versions", status_code=status.HTTP_201_CREATED)
async def new_version(publication_id: str, payload: VersionIn, request: Request,
                      user: CurrentUser, db: DbSession):
    """Publishes the agent as it stands now as the next version."""
    pub = await _mine(db, user, publication_id)
    if pub.status == PublicationStatus.revoked.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="revoked")
    agent = await _own_agent(db, user, pub.agent_id)
    catalogue, safe = await _model_context(user)
    checked = await publishing.preflight(db, user, agent, catalogue, safe)
    if not checked["ok"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail={"code": "cannot_publish", "problems": checked["problems"]})
    version = await publishing.make_version(db, user, pub, agent, checked["model"]["id"],
                                            payload.note)
    _audit(db, user, "publication.version", pub, request, version=version.number)
    await db.commit()
    return await get_publication(publication_id, user, db)


@router.post("/publications/{publication_id}/versions/{version_id}/activate")
async def activate_version(publication_id: str, version_id: str, request: Request,
                           user: CurrentUser, db: DbSession):
    """Rolls the bot back (or forward) to a version it had."""
    pub = await _mine(db, user, publication_id)
    version = await db.get(PublicationVersion, version_id)
    if version is None or version.publication_id != pub.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version_not_found")
    pub.active_version_id = version.id
    pub.updated_at = utcnow()
    db.add(pub)
    _audit(db, user, "publication.rollback", pub, request, version=version.number)
    await db.commit()
    return await get_publication(publication_id, user, db)


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
    for row in rows:
        if row.key.startswith("topic:"):
            topics[row.key[6:]] = topics.get(row.key[6:], 0) + int(row.value)
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
        "topics": [{"word": w, "count": c}
                   for w, c in sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))[:15]],
        "unanswered": [{"text": q.text, "at": q.at} for q in questions],
    }


def _conversation_row(session: ChatSession, turns: int) -> dict[str, Any]:
    return {"id": session.id, "title": publishing.masked(session.title or "", 80),
            "sharedAt": session.shared_at, "viewable": session.viewable,
            "updatedAt": session.updated_at, "createdAt": session.created_at, "turns": turns}


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
    counts = await _turn_counts(db, [s.id for s in rows])
    return [_conversation_row(s, int(counts.get(s.id, 0))) for s in rows[:200]]


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
    await db.commit()
    return {**_conversation_row(session, sum(1 for m in messages if m.role is Role.user)),
            "messages": publishing.masked_transcript(list(messages))}


# ── members ────────────────────────────────────────────────────────────


def _open_to(pub: Publication, user: User) -> bool:
    """Who may open the page: members while it is open or paused; its owner always."""
    if pub.owner_id == user.id:
        return pub.status != PublicationStatus.revoked.value
    return pub.visibility == PublicationVisibility.internal.value and pub.status in (
        PublicationStatus.approved.value, PublicationStatus.paused.value)


async def _bot(db: DbSession, slug: str, user: User) -> Publication:
    pub = (await db.exec(select(Publication).where(Publication.slug == slug[:80]))).first()
    if pub is None or not _open_to(pub, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot_not_found")
    return pub


async def _visitor(db: DbSession, pub: Publication, user: User) -> PublicationVisitor:
    row = (await db.exec(select(PublicationVisitor).where(
        PublicationVisitor.publication_id == pub.id,
        PublicationVisitor.user_id == user.id))).first()
    now = utcnow()
    if row is None:
        row = PublicationVisitor(publication_id=pub.id, user_id=user.id)
        db.add(row)
        await publishing.bump(db, pub.id, publishing.day_window(now), "visitors")
    elif publishing.day_window(row.last_seen_at) != publishing.day_window(now):
        await publishing.bump(db, pub.id, publishing.day_window(now), "visitors")
    row.last_seen_at = now
    db.add(row)
    return row


def _ready(pub: Publication, visitor: PublicationVisitor) -> None:
    if visitor.blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="visitor_blocked")
    if pub.status != PublicationStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    if visitor.consent_version != pub.consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="consent_required")


@router.get("/bots")
async def list_bots(user: CurrentUser, db: DbSession):
    """The bots members can open, and the caller's own."""
    rows = (await db.exec(select(Publication).where(
        ((Publication.visibility == PublicationVisibility.internal.value)
         & (Publication.status == PublicationStatus.approved.value))
        | ((Publication.owner_id == user.id)
           & (Publication.status != PublicationStatus.revoked.value)))
        .order_by(col(Publication.published_at).desc()))).all()
    owners = {u.id: u.name for u in (await db.exec(select(User).where(
        col(User.id).in_({p.owner_id for p in rows})))).all()} if rows else {}
    snaps = {v.id: v.agent_snapshot_id for v in (await db.exec(select(PublicationVersion).where(
        col(PublicationVersion.id).in_([p.active_version_id for p in rows
                                        if p.active_version_id])))).all()} if rows else {}
    colors = {a.id: a.color for a in (await db.exec(select(Agent).where(
        col(Agent.id).in_(list(snaps.values()))))).all()} if snaps else {}
    return [{"slug": p.slug, "title": p.title, "description": p.description,
             "ownerName": owners.get(p.owner_id, ""), "mine": p.owner_id == user.id,
             "id": p.id if p.owner_id == user.id else None, "status": p.status,
             "color": colors.get(snaps.get(p.active_version_id or "", ""), "#5b53e8"),
             "publishedAt": p.published_at} for p in rows]


@router.get("/b/{slug}")
async def bot_page(slug: str, user: CurrentUser, db: DbSession):
    pub = await _bot(db, slug, user)
    visitor = await _visitor(db, pub, user)
    owner = await db.get(User, pub.owner_id)
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    snap = await db.get(Agent, version.agent_snapshot_id) if version else None
    await db.commit()
    return {
        "slug": pub.slug, "title": pub.title, "description": pub.description,
        "ownerName": owner.name if owner else "", "mine": pub.owner_id == user.id,
        "status": pub.status, "pauseReason": pub.pause_reason if pub.owner_id == user.id else "",
        "branding": _branding(pub.branding), "showSources": pub.show_sources,
        "showProcess": pub.show_process, "conversationView": pub.conversation_view,
        "retentionDays": pub.retention_days, "consentVersion": pub.consent_version,
        "consented": visitor.consent_version == pub.consent_version,
        "color": snap.color if snap else "#5b53e8",
        "maxInputChars": publishing.limits_of(pub)["maxInputChars"],
    }


@router.post("/b/{slug}/consent", status_code=status.HTTP_204_NO_CONTENT)
async def consent(slug: str, payload: ConsentIn, request: Request, user: CurrentUser,
                  db: DbSession):
    """The visitor read the notice (and, with 「대화 열람」 on, agreed to it)."""
    pub = await _bot(db, slug, user)
    if payload.version != pub.consent_version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="consent_outdated")
    visitor = await _visitor(db, pub, user)
    visitor.consent_version = pub.consent_version
    visitor.consented_at = utcnow()
    db.add(visitor)
    if pub.conversation_view == "on":
        _audit(db, user, "publication.consent", pub, request, version=pub.consent_version)
    await db.commit()


async def _visited(db: DbSession, pub: Publication, user: User,
                   session_id: str) -> tuple[PublicationVisitor, ChatSession]:
    visitor = await _visitor(db, pub, user)
    session = await db.get(ChatSession, session_id)
    if session is None or session.publication_id != pub.id or session.visitor_id != visitor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return visitor, session


@router.get("/b/{slug}/conversations")
async def my_conversations(slug: str, user: CurrentUser, db: DbSession):
    pub = await _bot(db, slug, user)
    visitor = await _visitor(db, pub, user)
    rows = (await db.exec(select(ChatSession).where(
        ChatSession.publication_id == pub.id, ChatSession.visitor_id == visitor.id)
        .order_by(col(ChatSession.updated_at).desc()).limit(50))).all()
    await db.commit()
    return [{"id": s.id, "title": s.title, "updatedAt": s.updated_at, "sharedAt": s.shared_at}
            for s in rows]


@router.post("/b/{slug}/conversations", status_code=status.HTTP_201_CREATED)
async def start_conversation(slug: str, user: CurrentUser, db: DbSession):
    pub = await _bot(db, slug, user)
    visitor = await _visitor(db, pub, user)
    _ready(pub, visitor)
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    if version is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
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
async def my_conversation(slug: str, session_id: str, user: CurrentUser, db: DbSession):
    pub = await _bot(db, slug, user)
    _, session = await _visited(db, pub, user, session_id)
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
async def delete_my_conversation(slug: str, session_id: str, user: CurrentUser,
                                 db: DbSession):
    pub = await _bot(db, slug, user)
    _, session = await _visited(db, pub, user, session_id)
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
                       user: CurrentUser, db: DbSession):
    """A visitor's question, answered by the bot's fixed version on its owner's account."""
    from app.routers import sessions

    pub = await _bot(db, slug, user)
    visitor, session = await _visited(db, pub, user, session_id)
    _ready(pub, visitor)
    limits = publishing.limits_of(pub)
    if len(payload.content) > limits["maxInputChars"]:
        raise HTTPException(status_code=422,
                            detail={"code": "too_long", "max": limits["maxInputChars"]})
    asked = int((await db.exec(select(func.count()).select_from(Message).where(
        Message.session_id == session.id, Message.role == Role.user))).one())
    if payload.retry_of is None and asked >= limits["turnsPerConversation"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="conversation_full")
    owner = await db.get(User, pub.owner_id)
    if reason := publishing.owner_blocks(owner):
        publishing.pause(db, pub, reason)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    version = await db.get(PublicationVersion, pub.active_version_id) \
        if pub.active_version_id else None
    if version is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="bot_paused")
    # The conversation runs on the version in force: a rollback reaches it at once.
    session.agent_id = version.agent_snapshot_id
    session.model = version.model
    session.publication_version_id = version.id
    db.add(session)
    try:
        await publishing.admit(db, pub, visitor.id)
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
        model_id=version.model, question=payload.content, slot_id=slot,
        first_turn=asked == 0)
    token = sessions._PUBLISHED.set(turn)
    try:
        response = await sessions.send_message(
            session.id, SendMessage(content=payload.content, retry_of=payload.retry_of),
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
    response.body_iterator = _visitor_stream(response.body_iterator, pub)
    return response


@router.post("/b/{slug}/conversations/{session_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def visitor_stop(slug: str, session_id: str, user: CurrentUser, db: DbSession):
    from app.routers import sessions

    pub = await _bot(db, slug, user)
    _, session = await _visited(db, pub, user, session_id)
    await db.commit()
    sessions.fire_stop_locally(session.id)
    await stop_signal.broadcast(session.id)
    await sessions._release_turn(session.id, token="")


@router.patch("/b/{slug}/messages/{message_id}/rating")
async def visitor_rate(slug: str, message_id: str, payload: VisitorRating, user: CurrentUser,
                       db: DbSession):
    """👍👎 on one answer; the owner sees the counts, not the answer."""
    pub = await _bot(db, slug, user)
    message = await db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    await _visited(db, pub, user, message.session_id)
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
async def visitor_share(slug: str, session_id: str, request: Request, user: CurrentUser,
                        db: DbSession):
    """「만든 사람에게 보내기」: this one conversation becomes readable to the bot's maker,
    personal details masked."""
    pub = await _bot(db, slug, user)
    _, session = await _visited(db, pub, user, session_id)
    if session.shared_at is None:
        session.shared_at = utcnow()
        db.add(session)
        await publishing.bump(db, pub.id, publishing.day_window(), "shared")
        _audit(db, user, "publication.shared", pub, request, sessionId=session.id)
    await db.commit()
    return {"id": session.id, "sharedAt": session.shared_at}


@router.delete("/b/{slug}/conversations/{session_id}/share")
async def visitor_unshare(slug: str, session_id: str, user: CurrentUser, db: DbSession):
    pub = await _bot(db, slug, user)
    _, session = await _visited(db, pub, user, session_id)
    session.shared_at = None
    db.add(session)
    await db.commit()
    return {"id": session.id, "sharedAt": None}


# ── administrators ─────────────────────────────────────────────────────


@router.get("/admin/publications")
async def admin_list(admin: AdminUser, db: DbSession):
    rows = (await db.exec(select(Publication).order_by(
        col(Publication.updated_at).desc()))).all()
    owners = {u.id: u for u in (await db.exec(select(User).where(
        col(User.id).in_({p.owner_id for p in rows})))).all()} if rows else {}
    counts = dict((await db.exec(select(ChatSession.publication_id, func.count()).where(
        col(ChatSession.publication_id).in_([p.id for p in rows])).group_by(
        col(ChatSession.publication_id)))).all()) if rows else {}
    return [{**_pub_out(p, owner_name=owners[p.owner_id].name if p.owner_id in owners else ""),
             "ownerEmail": owners[p.owner_id].email if p.owner_id in owners else "",
             "conversations": int(counts.get(p.id, 0))} for p in rows]


@router.post("/admin/publications/{publication_id}/status")
async def admin_status(publication_id: str, payload: AdminStatusIn, request: Request,
                       admin: AdminUser, db: DbSession):
    pub = await db.get(Publication, publication_id)
    if pub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    if payload.status == "paused":
        publishing.pause(db, pub, "admin", actor_id=admin.id)
    elif payload.status == "revoked":
        pub.status, pub.pause_reason = PublicationStatus.revoked.value, "admin"
    else:
        pub.status, pub.pause_reason = PublicationStatus.approved.value, ""
    pub.updated_at = utcnow()
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
    _audit(db, admin, "publication.admin_list", pub, request, severity="warn", reason=why)
    await db.commit()
    return [_conversation_row(s, int(counts.get(s.id, 0))) for s in rows]


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
    return {**_conversation_row(session, sum(1 for m in messages if m.role is Role.user)),
            "messages": publishing.masked_transcript(list(messages))}


__all__ = ["router"]

