"""Search over the caller's own conversations and results; DB only, no model or outside call."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import and_, case, func, literal, not_, or_, text, true
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from app.core.deps import CurrentUser, DbSession
from app.models.chat import ChatSession, Message, Role, owned_by
from app.models.workspace import Artifact
from app.schemas.auth import Wire
from app.services import search_text

router = APIRouter(tags=["search"])

#: chars read around the first match before cutting
_WINDOW = 480


class Run(Wire):
    text: str
    hit: bool


class SessionHit(Wire):
    id: str
    kind: str
    title: str
    title_parts: list[Run]
    matched_in: Literal["title", "message"]
    #: latest message holding every word; empty for title match
    snippet: list[Run]
    message_id: str | None = None
    role: str | None = None
    project_id: str | None = None
    artifact_id: str | None = None
    updated_at: datetime


class ArtifactHit(Wire):
    id: str
    kind: str
    title: str
    title_parts: list[Run]
    matched_in: Literal["title", "text"]
    snippet: list[Run]
    session_id: str | None = None
    project_id: str | None = None
    updated_at: datetime


class SearchOut(Wire):
    query: str
    terms: list[str]
    sessions: list[SessionHit]
    artifacts: list[ArtifactHit]
    sessions_more: bool = False
    artifacts_more: bool = False
    #: only when `count=true`
    sessions_total: int | None = None


def _cased(word: str) -> bool:
    """Case-sensitive script? Hangul/digits → plain LIKE (faster than ILIKE)."""
    return word.lower() != word.upper()


def _position(dialect: str, column: Any, needle: str) -> ColumnElement:
    """1-based case-insensitive position; 0 if absent."""
    haystack = func.lower(column) if _cased(needle) else column
    if dialect == "postgresql":
        return func.strpos(haystack, needle)
    return func.instr(haystack, needle)


def _window(dialect: str, column: Any, needle: str) -> tuple[ColumnElement, ColumnElement]:
    """Bounded slice around `needle` and its 1-based start."""
    place = _position(dialect, column, needle)
    start = case((place > _WINDOW // 3, place - _WINDOW // 3), else_=1)
    return func.substr(column, start, _WINDOW), start


def _has(column: Any, word: str) -> ColumnElement:
    """Case-insensitive contains; trigram index where present."""
    pattern = search_text.like_pattern(word)
    if _cased(word):
        return col(column).ilike(pattern, escape="\\")
    return col(column).like(pattern, escape="\\")


def _all(column: Any, words: list[str]) -> ColumnElement:
    return and_(*[_has(column, w) for w in words])


def _cut(start: int | None, length: int | None) -> tuple[bool, bool]:
    begin = int(start or 1)
    return begin > 1, (length or 0) > begin - 1 + _WINDOW


@router.get("/search", response_model=SearchOut)
async def search(
    user: CurrentUser,
    db: DbSession,
    q: str = Query("", max_length=search_text.MAX_QUERY),
    scope: Literal["all", "sessions", "artifacts"] = "all",
    limit: int = Query(8, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100_000),
    count: bool = False,
):
    words = search_text.terms(q)
    out = SearchOut(query=q.strip(), terms=words, sessions=[], artifacts=[])
    if not words:
        return out
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        # per-word plan: asyncpg generic plans miss trigram vs short-word difference
        await db.exec(text("SET LOCAL plan_cache_mode = force_custom_plan"))
    # excerpt anchor: longest (rarest) word
    anchor = max(words, key=len)

    if scope in ("all", "sessions"):
        rows, out.sessions_more = await _sessions(db, user.id, dialect, words, anchor,
                                                  limit, offset)
        out.sessions = [_session_hit(row, words) for row in rows]
        if count:
            out.sessions_total = await _sessions_total(db, user.id, words)
    if scope in ("all", "artifacts"):
        rows, out.artifacts_more = await _artifacts(db, user.id, dialect, words, anchor,
                                                    limit, offset)
        out.artifacts = [_artifact_hit(row, words) for row in rows]
    return out


#: shorter words have no trigram
_TRIGRAM = 3

_SESSION_COLUMNS = (
    ChatSession.id, ChatSession.kind, ChatSession.title, ChatSession.project_id,
    ChatSession.artifact_id, ChatSession.updated_at,
)


def _message_columns(dialect: str, anchor: str) -> list[Any]:
    excerpt, start = _window(dialect, Message.content, anchor)
    return [
        col(Message.id).label("message_id"),
        col(Message.role).label("role"),
        excerpt.label("excerpt"),
        start.label("start"),
        func.length(Message.content).label("length"),
    ]


def _message_match(words: list[str]) -> ColumnElement:
    return and_(col(Message.role).in_([Role.user, Role.assistant]), _all(Message.content, words))


async def _sessions(db, user_id: str, dialect: str, words: list[str], anchor: str,
                    limit: int, offset: int):
    """Title matches first, then message matches; each newest first; shared `offset`."""
    in_title = _all(ChatSession.title, words)
    mine = owned_by(user_id)
    titled_total = int((await db.exec(select(func.count()).where(mine, in_title))).one())
    rows: list[SimpleNamespace] = []
    if offset < titled_total:
        page = (await db.exec(
            select(*_SESSION_COLUMNS).where(mine, in_title)
            .order_by(col(ChatSession.updated_at).desc(), col(ChatSession.id))
            .offset(offset).limit(limit + 1)
        )).all()
        found = await _latest_messages(db, dialect, words, anchor, [r.id for r in page])
        rows = [SimpleNamespace(**r._mapping, in_title=True, **found.get(r.id, _NO_MESSAGE))
                for r in page]
    wanted = limit + 1 - len(rows)
    if wanted > 0:
        skip = max(0, offset - titled_total)
        rows += await _message_hits(db, dialect, words, anchor, mine, in_title, skip, wanted)
    return rows[:limit], len(rows) > limit


async def _sessions_total(db, user_id: str, words: list[str]) -> int:
    """Matching conversation count (title or message)."""
    in_title = _all(ChatSession.title, words)
    mine = owned_by(user_id)
    titled = int((await db.exec(select(func.count()).where(mine, in_title))).one())
    by_message = int((await db.exec(
        select(func.count(func.distinct(Message.session_id)))
        .join(ChatSession, col(ChatSession.id) == col(Message.session_id))
        .where(mine, not_(in_title), _message_match(words))
    )).one())
    return titled + by_message


_NO_MESSAGE: dict[str, Any] = {
    "message_id": None, "role": None, "excerpt": None, "start": None, "length": None,
}


async def _latest_messages(db, dialect: str, words: list[str], anchor: str,
                           session_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Newest all-words message per conversation in a page."""
    if not session_ids:
        return {}
    rows = (await db.exec(
        select(col(Message.session_id).label("session_id"), *_message_columns(dialect, anchor))
        .where(col(Message.session_id).in_(session_ids), _message_match(words))
        .order_by(col(Message.session_id), col(Message.created_at).desc())
    )).all()
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        values = dict(row._mapping)
        found.setdefault(values.pop("session_id"), values)
    return found


async def _message_hits(db, dialect: str, words: list[str], anchor: str, mine: ColumnElement,
                        in_title: ColumnElement, skip: int, wanted: int) -> list[SimpleNamespace]:
    """Non-title-matched conversations whose messages match, newest first."""
    if dialect == "postgresql" and all(len(w) < _TRIGRAM for w in words):
        # short words: no trigram → walk conversations newest first, stop when page full
        latest = (
            select(*_message_columns(dialect, anchor))
            .where(col(Message.session_id) == col(ChatSession.id), _message_match(words))
            .order_by(col(Message.created_at).desc())
            .limit(1)
            .lateral("hit")
        )
        query = (
            select(*_SESSION_COLUMNS, latest)
            .join(latest, true())
            .where(mine, not_(in_title))
            # (user, updated_at) index order; id tie-break via incremental sort
            .order_by(col(ChatSession.updated_at).desc(), col(ChatSession.id))
        )
    else:
        # trigram index (or scan off PG); newest per conversation
        ranked = (
            select(
                col(Message.session_id).label("session_id"),
                *_message_columns(dialect, anchor),
                func.row_number()
                .over(partition_by=Message.session_id, order_by=col(Message.created_at).desc())
                .label("rank"),
            )
            .join(ChatSession, col(ChatSession.id) == col(Message.session_id))
            .where(mine, _message_match(words))
            .subquery()
        )
        best = select(ranked).where(ranked.c.rank == 1).subquery()
        query = (
            select(*_SESSION_COLUMNS, best.c.message_id, best.c.role, best.c.excerpt,
                   best.c.start, best.c.length)
            .join(best, best.c.session_id == col(ChatSession.id))
            .where(mine, not_(in_title))
            .order_by(col(ChatSession.updated_at).desc(), col(ChatSession.id))
        )
    rows = (await db.exec(query.offset(skip).limit(wanted))).all()
    return [SimpleNamespace(**row._mapping, in_title=False) for row in rows]


async def _artifacts(db, user_id: str, dialect: str, words: list[str], anchor: str,
                     limit: int, offset: int):
    excerpt, start = _window(dialect, Artifact.search_text, anchor)
    # title-all-words ranks first
    matches = and_(*[or_(_has(Artifact.title, w), _has(Artifact.search_text, w)) for w in words])
    in_title = _all(Artifact.title, words)
    query = (
        select(
            Artifact.id, Artifact.kind, Artifact.title, Artifact.session_id, Artifact.project_id,
            Artifact.updated_at, in_title.label("in_title"),
            excerpt.label("excerpt"), start.label("start"),
            func.length(Artifact.search_text).label("length"),
            (_position(dialect, Artifact.search_text, anchor) > literal(0)).label("in_text"),
        )
        .where(Artifact.user_id == user_id, matches)
        .order_by(case((in_title, 0), else_=1), col(Artifact.updated_at).desc(),
                  col(Artifact.id))
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await db.exec(query)).all()
    return rows[:limit], len(rows) > limit


def _session_hit(row: Any, words: list[str]) -> SessionHit:
    snippet: list[dict[str, Any]] = []
    if row.message_id is not None:
        before, after = _cut(row.start, row.length)
        snippet = search_text.excerpt(row.excerpt or "", words, cut_before=before,
                                      cut_after=after)
    kind = row.kind.value if hasattr(row.kind, "value") else str(row.kind)
    role = row.role.value if hasattr(row.role, "value") else row.role
    return SessionHit(
        id=row.id, kind=kind, title=row.title or "",
        title_parts=search_text.parts(row.title or "", words),
        matched_in="title" if row.in_title else "message",
        snippet=snippet, message_id=row.message_id, role=role,
        project_id=row.project_id, artifact_id=row.artifact_id, updated_at=row.updated_at,
    )


def _artifact_hit(row: Any, words: list[str]) -> ArtifactHit:
    snippet: list[dict[str, Any]] = []
    if row.in_text:
        before, after = _cut(row.start, row.length)
        snippet = search_text.excerpt(row.excerpt or "", words, cut_before=before,
                                      cut_after=after)
    kind = row.kind.value if hasattr(row.kind, "value") else str(row.kind)
    return ArtifactHit(
        id=row.id, kind=kind, title=row.title or "",
        title_parts=search_text.parts(row.title or "", words),
        matched_in="title" if row.in_title else "text",
        snippet=snippet, session_id=row.session_id, project_id=row.project_id,
        updated_at=row.updated_at,
    )
