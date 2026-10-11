"""「문의·신고」: user reports (own only) and admin queue; snapshot opens audited."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import false, func, or_
from sqlmodel import col, select

from app.core import logs
from app.core.deps import AdminUser, CurrentUser, CurrentViewer, DbSession, client_ip
from app.models.chat import ChatSession
from app.models.issues import IssueKind, IssueReply, IssueReport, IssueStatus
from app.models.user import AuditEvent, User, UserRole, utcnow
from app.models.workspace import Artifact, StoredFile
from app.schemas.auth import Wire
from app.services import audit as audit_service
from app.services import files as file_service
from app.services import issue_snapshot

log = logging.getLogger(__name__)

router = APIRouter(tags=["issues"])

API_VERSION = "0.1.0"
_MAX_BODY = 4000
_MAX_REPLY = 2000
#: raster only, by bytes
SCREENSHOT_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
#: per reporter: reports short+long window, replies short
RATE_WINDOW = timedelta(minutes=10)
RATE_REPORTS = 5
RATE_DAY_REPORTS = 30
RATE_REPLIES = 20

#: status workflow; on_hold needs reason; resolved reopenable
TRANSITIONS: dict[str, frozenset[str]] = {
    IssueStatus.received: frozenset({IssueStatus.checking, IssueStatus.resolved,
                                     IssueStatus.on_hold}),
    IssueStatus.checking: frozenset({IssueStatus.resolved, IssueStatus.on_hold}),
    IssueStatus.on_hold: frozenset({IssueStatus.checking, IssueStatus.resolved}),
    IssueStatus.resolved: frozenset({IssueStatus.checking}),
}


class IssueIn(Wire):
    kind: IssueKind = IssueKind.bug
    body: str = Field(min_length=1, max_length=_MAX_BODY)
    session_id: str | None = Field(default=None, max_length=64)
    #: unticked → no session content attached
    share_session: bool = False
    #: extra confirmation for strict-local sessions
    confirm_internal: bool = False
    message_id: str | None = Field(default=None, max_length=64)
    artifact_id: str | None = Field(default=None, max_length=64)
    #: `POST /files` upload, moved into the report
    screenshot_file_id: str | None = Field(default=None, max_length=64)
    client: dict[str, Any] | None = None


class ReplyIn(Wire):
    body: str = Field(min_length=1, max_length=_MAX_REPLY)


class IssuePatch(Wire):
    status: IssueStatus | None = None
    hold_reason: str | None = Field(default=None, max_length=400)
    #: admin id; "" clears
    assignee_id: str | None = Field(default=None, max_length=64)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _unread(row: IssueReport) -> bool:
    if row.admin_updated_at is None:
        return False
    return row.user_seen_at is None or row.admin_updated_at > row.user_seen_at


def _snapshot_meta(row: IssueReport) -> dict[str, Any] | None:
    snap = row.snapshot
    if not row.session_shared or not isinstance(snap, dict):
        return None
    return {
        "title": (snap.get("session") or {}).get("title", ""),
        "kind": (snap.get("session") or {}).get("kind", ""),
        "messages": len(snap.get("messages") or []),
        "artifacts": len(snap.get("artifacts") or []),
        "files": len(snap.get("files") or []),
        "bytes": row.snapshot_bytes,
        "truncated": snap.get("truncated") or [],
    }


def _out(row: IssueReport, *, reporter: User | None = None,
         assignee: User | None = None, replies: int | None = None) -> dict[str, Any]:
    return {
        "id": row.id, "kind": row.kind, "status": row.status, "body": row.body,
        "holdReason": row.hold_reason,
        "sessionId": row.session_id, "messageId": row.message_id, "artifactId": row.artifact_id,
        "sessionShared": row.session_shared,
        "hasScreenshot": bool(row.screenshot_key),
        "snapshot": _snapshot_meta(row),
        "unread": _unread(row),
        "assignee": {"id": assignee.id, "name": assignee.name} if assignee else None,
        "reporter": ({"id": reporter.id, "name": reporter.name, "email": reporter.email}
                     if reporter else None),
        "replies": replies,
        "createdAt": _iso(row.created_at), "updatedAt": _iso(row.updated_at),
        "adminUpdatedAt": _iso(row.admin_updated_at),
    }


def _reply_out(reply: IssueReply) -> dict[str, Any]:
    return {
        "id": reply.id, "authorRole": reply.author_role, "authorName": reply.author_name,
        "body": reply.body, "status": reply.status, "createdAt": _iso(reply.created_at),
    }


async def _replies(db: DbSession, report_id: str) -> list[IssueReply]:
    return list((await db.exec(
        select(IssueReply).where(IssueReply.report_id == report_id)
        .order_by(col(IssueReply.created_at), col(IssueReply.id))
    )).all())


async def _own_report(db: DbSession, user: User, report_id: str) -> IssueReport:
    row = await db.get(IssueReport, report_id)
    # same answer for missing and foreign
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issue_not_found")
    return row


async def _owned_session(db: DbSession, user: User, session_id: str) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id or session.publication_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session_not_found")
    return session


async def _count_since(db: DbSession, column, owner_column, owner: str, since: datetime) -> int:
    return int((await db.exec(
        select(func.count()).where(owner_column == owner, column >= since)
    )).one())


async def _rate_limit_reports(db: DbSession, user: User) -> None:
    now = utcnow()
    recent = await _count_since(db, col(IssueReport.created_at), IssueReport.user_id, user.id,
                                now - RATE_WINDOW)
    daily = await _count_since(db, col(IssueReport.created_at), IssueReport.user_id, user.id,
                               now - timedelta(days=1))
    if recent >= RATE_REPORTS or daily >= RATE_DAY_REPORTS:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="issue_rate_limited")


async def _rate_limit_replies(db: DbSession, user: User) -> None:
    recent = await _count_since(db, col(IssueReply.created_at), IssueReply.author_id, user.id,
                                utcnow() - RATE_WINDOW)
    if recent >= RATE_REPLIES:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="issue_rate_limited")


@router.get("/issues/session-info/{session_id}")
async def session_info(session_id: str, user: CurrentUser, db: DbSession):
    """Form preview of the attachable session; nothing stored."""
    session = await _owned_session(db, user, session_id)
    loaded = await issue_snapshot.load(db, session)
    return {
        "id": session.id, "title": session.title,
        "kind": str(getattr(session.kind, "value", session.kind)),
        "messageCount": len(loaded.messages), "artifactCount": len(loaded.artifacts),
        "fileCount": len(loaded.files), "runCount": len(loaded.runs),
        "strictLocal": issue_snapshot.strict_local(loaded),
        "models": issue_snapshot._models(loaded),
        "agent": loaded.agent, "pipeline": loaded.pipeline,
    }


async def _take_screenshot(db: DbSession, user: User, file_id: str,
                           report_id: str) -> tuple[str, str, str, StoredFile]:
    stored = await db.get(StoredFile, file_id)
    if stored is None or stored.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="screenshot_not_found")
    try:
        size = file_service.blob_size(stored.storage_key)
        if size > MAX_SCREENSHOT_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail="screenshot_too_large")
        with (file_service.storage_root() / stored.storage_key).open("rb") as blob:
            head = blob.read(64)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="screenshot_not_found") from None
    found = file_service.sniff(stored.name, head)
    if found not in SCREENSHOT_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="screenshot_not_image")
    key = file_service.copy_blob(user.id, f"issue-{report_id}-shot", stored.name,
                                 stored.storage_key)
    return key, stored.name, found, stored


@router.post("/issues", status_code=status.HTTP_201_CREATED)
async def create_issue(payload: IssueIn, user: CurrentUser, db: DbSession):
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty_body")
    await _rate_limit_reports(db, user)

    loaded = None
    if payload.session_id:
        session = await _owned_session(db, user, payload.session_id)
        loaded = await issue_snapshot.load(db, session)
    elif payload.share_session or payload.message_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="session_required")
    if payload.message_id and (
        loaded is None or payload.message_id not in {m.id for m in loaded.messages}
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message_not_found")
    if payload.artifact_id:
        artifact = await db.get(Artifact, payload.artifact_id)
        if artifact is None or artifact.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="artifact_not_found")
    share = bool(payload.share_session and loaded is not None)
    if share and issue_snapshot.strict_local(loaded) and not payload.confirm_internal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="internal_data_confirm_required")

    report_id = uuid4().hex
    copied: list[str] = []
    row = IssueReport(
        id=report_id, user_id=user.id, kind=payload.kind.value, body=body,
        session_id=loaded.session.id if loaded else None,
        message_id=payload.message_id, artifact_id=payload.artifact_id,
        session_shared=share,
        context=issue_snapshot.context(
            loaded, client=issue_snapshot.client_info(payload.client), api_version=API_VERSION,
            message_id=payload.message_id, artifact_id=payload.artifact_id),
    )
    loose_upload: StoredFile | None = None
    try:
        if share:
            snapshot, copied = issue_snapshot.build(
                loaded, owner_id=user.id, report_id=report_id,
                message_id=payload.message_id, artifact_id=payload.artifact_id)
            row.snapshot = snapshot
            row.snapshot_bytes = issue_snapshot.size_of(snapshot)
        if payload.screenshot_file_id:
            key, name, mime, stored = await _take_screenshot(
                db, user, payload.screenshot_file_id, report_id)
            copied.append(key)
            row.screenshot_key, row.screenshot_name, row.screenshot_mime = key, name, mime
            # report-only upload: removed from file box after copy
            if not (stored.project_id or stored.session_id or stored.agent_id):
                loose_upload = stored
        db.add(row)
        if loose_upload is not None:
            await db.delete(loose_upload)
        await db.commit()
    except BaseException:
        for key in copied:
            file_service.delete_blob(key)
        raise
    if loose_upload is not None:
        file_service.delete_blob(loose_upload.storage_key)
    await db.refresh(row)
    log.info("issue %s filed (%s, shared=%s)", row.id, row.kind, share)
    return _out(row, replies=0)


@router.get("/issues")
async def list_issues(user: CurrentUser, db: DbSession):
    rows = (await db.exec(
        select(IssueReport).where(IssueReport.user_id == user.id)
        .order_by(col(IssueReport.created_at).desc()).limit(200)
    )).all()
    counts = await _reply_counts(db, [r.id for r in rows])
    return [_out(r, replies=counts.get(r.id, 0)) for r in rows]


@router.get("/issues/unread")
async def unread_issues(user: CurrentUser, db: DbSession):
    """Count of my reports with unread admin activity."""
    count = (await db.exec(
        select(func.count()).where(
            IssueReport.user_id == user.id,
            col(IssueReport.admin_updated_at).is_not(None),
            or_(col(IssueReport.user_seen_at).is_(None),
                col(IssueReport.admin_updated_at) > col(IssueReport.user_seen_at)),
        )
    )).one()
    return {"count": int(count)}


@router.get("/issues/{report_id}")
async def get_issue(report_id: str, user: CurrentUser, db: DbSession):
    row = await _own_report(db, user, report_id)
    replies = await _replies(db, row.id)
    out = _out(row, replies=len(replies))
    out["thread"] = [_reply_out(r) for r in replies]
    return out


@router.post("/issues/{report_id}/read")
async def read_issue(report_id: str, user: CurrentUser, db: DbSession):
    row = await _own_report(db, user, report_id)
    row.user_seen_at = utcnow()
    db.add(row)
    await db.commit()
    return {"ok": True}


@router.post("/issues/{report_id}/replies", status_code=status.HTTP_201_CREATED)
async def reply_as_reporter(report_id: str, payload: ReplyIn, user: CurrentUser, db: DbSession):
    row = await _own_report(db, user, report_id)
    await _rate_limit_replies(db, user)
    text = payload.body.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty_body")
    reply = IssueReply(report_id=row.id, author_id=user.id, author_role="user",
                       author_name=user.name, body=text)
    now = utcnow()
    row.updated_at, row.user_seen_at = now, now
    db.add_all([reply, row])
    await db.commit()
    await db.refresh(reply)
    return _reply_out(reply)


def _image_response(row: IssueReport) -> Response:
    if not row.screenshot_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="screenshot_not_found")
    try:
        data = file_service.read_blob(row.screenshot_key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="blob_missing") from None
    media, inline = file_service.served_as(row.screenshot_name, row.screenshot_mime, data)
    return Response(content=data, media_type=media,
                    headers=file_service.download_headers(media, inline, row.screenshot_name))


@router.get("/issues/{report_id}/screenshot")
async def my_screenshot(report_id: str, user: CurrentViewer, db: DbSession):
    return _image_response(await _own_report(db, user, report_id))


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _reply_counts(db: DbSession, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    return dict((await db.exec(
        select(IssueReply.report_id, func.count())
        .where(col(IssueReply.report_id).in_(ids))
        .group_by(col(IssueReply.report_id))
    )).all())


async def _admins(db: DbSession) -> list[User]:
    return list((await db.exec(
        select(User).where(User.role == UserRole.admin).order_by(col(User.name))
    )).all())


def _audit(db: DbSession, request: Request, admin: User, action: str, row: IssueReport,
           detail: str = "", *, changes: dict | None = None) -> None:
    metadata: dict[str, Any] = {"issueId": row.id}
    if changes:
        metadata["changes"] = changes
    db.add(AuditEvent(
        actor_id=admin.id, action=action, target=f"issue:{row.id[:12]}", detail=detail[:200],
        event_metadata=metadata, ip=client_ip(request),
        user_agent=request.headers.get("User-Agent", "")[:400],
    ))


@router.get("/admin/issues")
async def admin_list(
    admin: AdminUser,
    db: DbSession,
    kind: str | None = Query(None, max_length=20),
    status_: str | None = Query(None, alias="status", max_length=20),
    since: datetime | None = None,
    until: datetime | None = None,
    user: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=200),
    assignee: str | None = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    size: int = Query(30, ge=1, le=100),
):
    """Newest first; `until` exclusive; `assignee` = id, `me` or `none`."""
    conds = []
    if kind and kind in IssueKind.__members__:
        conds.append(IssueReport.kind == kind)
    if status_ and status_ in IssueStatus.__members__:
        conds.append(IssueReport.status == status_)
    if since:
        conds.append(col(IssueReport.created_at) >= since)
    if until:
        conds.append(col(IssueReport.created_at) < until)
    if user and user.strip():
        pattern = f"%{_like(user.strip())}%"
        ids = list((await db.exec(select(User.id).where(or_(
            col(User.email).ilike(pattern, escape="\\"),
            col(User.name).ilike(pattern, escape="\\"),
        )).limit(500))).all())
        conds.append(col(IssueReport.user_id).in_(ids) if ids else false())
    if q and q.strip():
        conds.append(col(IssueReport.body).ilike(f"%{_like(q.strip())}%", escape="\\"))
    if assignee == "me":
        conds.append(IssueReport.assignee_id == admin.id)
    elif assignee == "none":
        conds.append(col(IssueReport.assignee_id).is_(None))
    elif assignee:
        conds.append(IssueReport.assignee_id == assignee)

    total = (await db.exec(select(func.count()).select_from(IssueReport).where(*conds))).one()
    rows = (await db.exec(
        select(IssueReport).where(*conds)
        .order_by(col(IssueReport.created_at).desc(), col(IssueReport.id).desc())
        .offset((page - 1) * size).limit(size)
    )).all()
    people_ids = {r.user_id for r in rows} | {r.assignee_id for r in rows if r.assignee_id}
    people = {u.id: u for u in (await db.exec(
        select(User).where(col(User.id).in_(people_ids)))).all()} if people_ids else {}
    counts = await _reply_counts(db, [r.id for r in rows])
    by_status = dict((await db.exec(
        select(IssueReport.status, func.count()).group_by(col(IssueReport.status))
    )).all())
    return {
        "items": [_out(r, reporter=people.get(r.user_id),
                       assignee=people.get(r.assignee_id or ""),
                       replies=counts.get(r.id, 0)) for r in rows],
        "total": int(total),
        "page": page,
        "size": size,
        "counts": {s.value: int(by_status.get(s.value, 0)) for s in IssueStatus},
        "admins": [{"id": a.id, "name": a.name} for a in await _admins(db)],
    }


async def _admin_report(db: DbSession, report_id: str) -> IssueReport:
    row = await db.get(IssueReport, report_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="issue_not_found")
    return row


async def _admin_detail(db: DbSession, row: IssueReport) -> dict[str, Any]:
    reporter = await db.get(User, row.user_id)
    assignee = await db.get(User, row.assignee_id) if row.assignee_id else None
    replies = await _replies(db, row.id)
    out = _out(row, reporter=reporter, assignee=assignee, replies=len(replies))
    out["thread"] = [_reply_out(r) for r in replies]
    out["context"] = row.context or {}
    out["admins"] = [{"id": a.id, "name": a.name} for a in await _admins(db)]
    out["transitions"] = sorted(TRANSITIONS.get(IssueStatus(row.status), frozenset()))
    return out


@router.get("/admin/issues/{report_id}")
async def admin_get(report_id: str, admin: AdminUser, db: DbSession):
    return await _admin_detail(db, await _admin_report(db, report_id))


@router.patch("/admin/issues/{report_id}")
async def admin_update(report_id: str, payload: IssuePatch, request: Request, admin: AdminUser,
                       db: DbSession):
    row = await _admin_report(db, report_id)
    before = {"status": row.status, "holdReason": row.hold_reason, "assignee": row.assignee_id}
    changes = payload.model_dump(exclude_unset=True)
    now = utcnow()
    if "assignee_id" in changes:
        wanted = (payload.assignee_id or "").strip() or None
        if wanted is not None:
            target = await db.get(User, wanted)
            if target is None or target.role is not UserRole.admin:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="assignee_not_admin")
        row.assignee_id = wanted
    if payload.status is not None and payload.status.value != row.status:
        allowed = TRANSITIONS.get(IssueStatus(row.status), frozenset())
        if payload.status not in allowed:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="issue_transition_refused")
        reason = (payload.hold_reason or "").strip()
        if payload.status is IssueStatus.on_hold and not reason:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="hold_reason_required")
        row.status = payload.status.value
        row.hold_reason = reason if payload.status is IssueStatus.on_hold else ""
        # change shown in reporter's thread (with hold reason)
        db.add(IssueReply(report_id=row.id, author_id=admin.id, author_role="admin",
                          author_name=admin.name, body=row.hold_reason, status=row.status))
        row.admin_updated_at = now
    after = {"status": row.status, "holdReason": row.hold_reason, "assignee": row.assignee_id}
    diff = audit_service.diff(before, after)
    if diff:
        row.updated_at = now
        db.add(row)
        _audit(db, request, admin, "issue.update", row, changes=diff)
        await db.commit()
        await db.refresh(row)
    return await _admin_detail(db, row)


@router.post("/admin/issues/{report_id}/replies", status_code=status.HTTP_201_CREATED)
async def reply_as_admin(report_id: str, payload: ReplyIn, admin: AdminUser, db: DbSession):
    row = await _admin_report(db, report_id)
    text = payload.body.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty_body")
    reply = IssueReply(report_id=row.id, author_id=admin.id, author_role="admin",
                       author_name=admin.name, body=text)
    now = utcnow()
    row.admin_updated_at = row.updated_at = now
    db.add_all([reply, row])
    await db.commit()
    await db.refresh(reply)
    return _reply_out(reply)


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Snapshot for display: storage keys → flags."""
    out = dict(snapshot)
    out["files"] = [
        {k: v for k, v in f.items() if k != "key"} | {"copied": bool(f.get("key"))}
        for f in snapshot.get("files") or []
    ]
    out["artifacts"] = [
        {k: v for k, v in a.items() if k != "blob"} | {"blobCopied": bool(a.get("blob"))}
        for a in snapshot.get("artifacts") or []
    ]
    return out


@router.get("/admin/issues/{report_id}/snapshot")
async def admin_snapshot(report_id: str, request: Request, admin: AdminUser, db: DbSession):
    """Shared session, read-only; each open audited."""
    row = await _admin_report(db, report_id)
    if not row.session_shared or not isinstance(row.snapshot, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="snapshot_not_shared")
    _audit(db, request, admin, "issue.snapshot_view", row,
           detail=str((row.snapshot.get("session") or {}).get("kind", "")))
    await db.commit()
    log.info("issue %s snapshot opened by %s", row.id, logs.safe(admin.email))
    return _public_snapshot(row.snapshot)


@router.get("/admin/issues/{report_id}/screenshot")
async def admin_screenshot(report_id: str, admin: AdminUser, db: DbSession):
    return _image_response(await _admin_report(db, report_id))


@router.get("/admin/issues/{report_id}/files/{index}")
async def admin_snapshot_file(report_id: str, index: int, admin: AdminUser, db: DbSession,
                              artifact: bool = False):
    """Snapshot blob: `files[index]`, or `artifacts[index]` with `artifact`."""
    row = await _admin_report(db, report_id)
    snapshot = row.snapshot if row.session_shared and isinstance(row.snapshot, dict) else {}
    entries = snapshot.get("artifacts" if artifact else "files") or []
    if not 0 <= index < len(entries):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
    entry = entries[index]
    key = entry.get("blob" if artifact else "key")
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_copied")
    try:
        data = file_service.read_blob(key)
    except OSError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="blob_missing") from None
    name = str(entry.get("name") or entry.get("title") or "file")
    media, inline = file_service.served_as(name, str(entry.get("mime") or ""), data)
    return Response(content=data, media_type=media,
                    headers=file_service.download_headers(media, inline, name))


__all__ = ["router"]
