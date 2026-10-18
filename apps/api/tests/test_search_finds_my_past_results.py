"""⌘K search over the caller's own conversations and results."""

from __future__ import annotations

import importlib.util
import json
import socket
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.models.chat import (
    ChatSession,
    Message,
    PipelineRun,
    Role,
    SessionKind,
    follow_document_title,
)
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.routers import search as search_router
from app.routers import sessions as sessions_router
from app.routers import workspace as workspace_router
from app.services import chat as chat_service
from app.services import search_text


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


ME = User(id="me", email="me@example.test", password_hash="unused", name="Me")
OTHER = User(id="other", email="other@example.test", password_hash="unused", name="Other")

REPORT_DATA = {
    "kind": "report",
    "templateId": "doc-brief",
    "sections": [
        {
            "id": "s1",
            "heading": "점검 개요",
            "format": "html",
            "status": "done",
            "content": (
                "<style>.danger{color:red}</style><p>방화벽 <b>정책</b> 42건을 점검했다.</p>"
            ),
        },
        {"id": "s2", "heading": "조치 계획", "content": "취약 계정 7개는 이번 달 안에 잠근다."},
    ],
    "design": {"accent": "#ff0000", "font": "gothic"},
}


@pytest.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    tables = [m.__table__ for m in (ChatSession, Message, Artifact, ArtifactVersion, PipelineRun)]
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))
    app = FastAPI()
    app.include_router(search_router.router)
    app.include_router(sessions_router.router)
    app.include_router(workspace_router.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    who = {"user": ME}
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: who["user"]
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        yield client, engine, who
    await engine.dispose()


async def _add(engine, *rows) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add_all(rows)
        await db.commit()


def _conversation(user: User, title: str, *texts: str, minutes: int = 0, **extra):
    at = utcnow() - timedelta(minutes=minutes)
    session = ChatSession(user_id=user.id, title=title, updated_at=at, created_at=at, **extra)
    messages = [
        Message(
            session_id=session.id,
            role=Role.user if i % 2 == 0 else Role.assistant,
            content=text,
            created_at=at + timedelta(seconds=i),
        )
        for i, text in enumerate(texts)
    ]
    return session, messages


def _text(runs: list[dict]) -> str:
    return "".join(r["text"] for r in runs)


def _hits(runs: list[dict]) -> list[str]:
    return [r["text"] for r in runs if r["hit"]]


# ── what search finds, and for whom ───────────────────────────────────


async def test_only_my_rows_are_searched(env):
    client, engine, _ = env
    mine, mine_msgs = _conversation(ME, "주간 회의", "방화벽 정책을 정리해 줘")
    theirs, their_msgs = _conversation(OTHER, "방화벽 정책 검토", "방화벽 정책 원문")
    await _add(
        engine, mine, theirs, *mine_msgs, *their_msgs,
        Artifact(user_id=OTHER.id, kind=ArtifactKind.report, title="방화벽 정책 보고서",
                 data=REPORT_DATA),
    )
    found = (await client.get("/search", params={"q": "방화벽 정책"})).json()
    assert [s["id"] for s in found["sessions"]] == [mine.id]
    assert found["artifacts"] == []
    # Each group alone, and every page of it, holds the same boundary.
    for scope in ("sessions", "artifacts"):
        for offset in (0, 1):
            page = (await client.get("/search", params={
                "q": "방화벽", "scope": scope, "offset": offset})).json()
            ids = {s["id"] for s in page["sessions"]} | {a["id"] for a in page["artifacts"]}
            assert theirs.id not in ids


async def test_a_message_match_shows_a_marked_window_of_it(env):
    client, engine, _ = env
    long_answer = (
        "앞부분 설명입니다. " * 60
        + "결론적으로 백업 주기는 매일 새벽 2시로 정한다. "
        + "뒷부분 이야기. " * 60
    )
    session, messages = _conversation(ME, "데이터 관리 상담", "백업은 어떻게 하지?", long_answer)
    await _add(engine, session, *messages)
    found = (await client.get("/search", params={"q": "새벽 백업"})).json()
    [hit] = found["sessions"]
    assert hit["matchedIn"] == "message"
    assert hit["messageId"] == messages[1].id and hit["role"] == "assistant"
    text = _text(hit["snippet"])
    assert "새벽" in text and "백업" in text
    # A window, not the whole answer, cut on both sides.
    assert len(text) <= search_text.SNIPPET + 4
    assert text.startswith("…") and text.endswith("…")
    assert set(_hits(hit["snippet"])) == {"새벽", "백업"}
    assert hit["titleParts"] == [{"text": "데이터 관리 상담", "hit": False}]


async def test_a_title_match_ranks_first_and_is_marked(env):
    client, engine, _ = env
    newer, newer_msgs = _conversation(ME, "잡담", "예산 이야기를 하자", minutes=1)
    older, older_msgs = _conversation(ME, "2027 예산 초안", "숫자를 정리", minutes=30)
    await _add(engine, newer, older, *newer_msgs, *older_msgs)
    found = (await client.get("/search", params={"q": "예산"})).json()
    assert [s["id"] for s in found["sessions"]] == [older.id, newer.id]
    assert found["sessions"][0]["matchedIn"] == "title"
    assert _hits(found["sessions"][0]["titleParts"]) == ["예산"]


async def test_every_word_must_be_present_and_case_is_ignored(env):
    client, engine, _ = env
    a, a_msgs = _conversation(ME, "A", "Kubernetes 클러스터 업그레이드")
    b, b_msgs = _conversation(ME, "B", "kubernetes 비용")
    await _add(engine, a, b, *a_msgs, *b_msgs)
    found = (await client.get("/search", params={"q": "KUBERNETES 업그레이드"})).json()
    assert [s["id"] for s in found["sessions"]] == [a.id]
    both = (await client.get("/search", params={"q": "kubernetes"})).json()
    assert {s["id"] for s in both["sessions"]} == {a.id, b.id}


async def test_like_wildcards_are_plain_characters(env):
    client, engine, _ = env
    a, a_msgs = _conversation(ME, "A", "할인율 30% 적용")
    b, b_msgs = _conversation(ME, "B", "아무 말")
    await _add(engine, a, b, *a_msgs, *b_msgs)
    assert [s["id"] for s in (await client.get("/search", params={"q": "%"})).json()["sessions"]] \
        == [a.id]
    assert (await client.get("/search", params={"q": "_"})).json()["sessions"] == []


async def test_pages_cover_every_match_once(env):
    client, engine, _ = env
    rows = []
    for i in range(12):
        # Four named with the word, eight that only say it.
        title = f"분기 실적 {i}" if i % 3 == 0 else f"대화 {i}"
        session, messages = _conversation(ME, title, f"{i}번 분기 실적 정리", minutes=i)
        rows += [session, *messages]
    await _add(engine, *rows)
    seen: list[str] = []
    more = []
    for offset in (0, 5, 10):
        page = (await client.get("/search", params={
            "q": "실적", "scope": "sessions", "limit": 5, "offset": offset})).json()
        seen += [s["id"] for s in page["sessions"]]
        more.append(page["sessionsMore"])
        assert page["artifacts"] == []
    assert len(seen) == 12 and len(set(seen)) == 12
    assert more == [True, True, False]
    # Titles first, across the page boundary.
    kinds = []
    for offset in (0, 5, 10):
        page = (await client.get("/search", params={
            "q": "실적", "scope": "sessions", "limit": 5, "offset": offset})).json()
        kinds += [s["matchedIn"] for s in page["sessions"]]
    assert kinds == ["title"] * 4 + ["message"] * 8


async def test_an_empty_or_oversized_query(env):
    client, _engine, _ = env
    empty = (await client.get("/search", params={"q": "   "})).json()
    assert empty["sessions"] == [] and empty["artifacts"] == [] and empty["terms"] == []
    assert (await client.get("/search", params={"q": "가" * 201})).status_code == 422
    assert (await client.get("/search", params={"q": "a", "limit": 51})).status_code == 422


async def test_a_result_is_found_by_its_words_not_its_styling(env):
    client, engine, _ = env
    report = Artifact(user_id=ME.id, kind=ArtifactKind.report, title="보안 점검 결과",
                      data=REPORT_DATA)
    await _add(engine, report)
    async with AsyncSession(engine) as db:
        stored = await db.get(Artifact, report.id)
        assert "방화벽 정책 42건" in stored.search_text
        assert "color" not in stored.search_text and "doc-brief" not in stored.search_text
    found = (await client.get("/search", params={"q": "취약 계정"})).json()
    [hit] = found["artifacts"]
    assert hit["id"] == report.id and hit["matchedIn"] == "text" and hit["kind"] == "report"
    assert set(_hits(hit["snippet"])) == {"취약", "계정"}
    assert (await client.get("/search", params={"q": "danger"})).json()["artifacts"] == []
    by_title = (await client.get("/search", params={"q": "보안 점검"})).json()["artifacts"]
    assert by_title[0]["matchedIn"] == "title"


async def test_the_text_follows_an_edit(env):
    client, engine, _ = env
    report = Artifact(user_id=ME.id, kind=ArtifactKind.report, title="메모", data=REPORT_DATA)
    await _add(engine, report)
    section = {"id": "s1", "heading": "새 절", "content": "망분리 예외"}
    edited = {**REPORT_DATA, "sections": [section]}
    response = await client.patch(f"/artifacts/{report.id}", json={"data": edited})
    assert response.status_code == 200, response.text
    assert [a["id"] for a in (await client.get("/search", params={"q": "망분리"})).json()[
        "artifacts"]] == [report.id]
    assert (await client.get("/search", params={"q": "방화벽"})).json()["artifacts"] == []


async def test_a_strict_local_conversation_is_searched_without_leaving_the_server(
    env, monkeypatch
):
    client, engine, _ = env
    session, messages = _conversation(
        ME, "내부 감사", "내부 자료: 서버 접근 기록 3건 누락", model="strict-local/qwen3.8-27b"
    )
    await _add(engine, session, *messages)

    def refuse(*_args, **_kwargs):
        raise AssertionError("search must not open a connection")

    import httpx

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    found = (await client.get("/search", params={"q": "접근 기록"})).json()
    assert [s["id"] for s in found["sessions"]] == [session.id]
    # Nothing in the router can reach a model or the index service.
    source = Path(search_router.__file__).read_text()
    for outward in ("litellm", "httpx", "index_client", "research", "tools_gateway"):
        assert outward not in source


# ── conversations named after their document ──────────────────────────


def test_a_document_names_its_report_conversation_but_not_a_chat_or_a_renamed_one():
    report = ChatSession(user_id="me", kind=SessionKind.report, title="사내 클라우드 보안 점검")
    assert follow_document_title(report, "  2026 하반기   클라우드 보안 점검 결과 ")
    assert report.title == "2026 하반기 클라우드 보안 점검 결과"
    chat = ChatSession(user_id="me", kind=SessionKind.chat, title="잡담")
    assert not follow_document_title(chat, "보고서 제목") and chat.title == "잡담"
    renamed = ChatSession(user_id="me", kind=SessionKind.slides, title="내 발표",
                          title_set_by_user=True)
    assert not follow_document_title(renamed, "덱 제목") and renamed.title == "내 발표"
    assert not follow_document_title(report, "   ")


async def test_storing_a_document_renames_the_conversation_until_the_person_does(env):
    client, engine, _ = env
    prompt = "사내 클라우드 보안 점검 결과를 정리한 1쪽 보고서. 점검 항목 5개, 조치 계획 포함"
    session = ChatSession(user_id=ME.id, kind=SessionKind.report,
                          title=chat_service.provisional_title(prompt))
    await _add(engine, session)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.get(ChatSession, session.id)
        artifact_id = await sessions_router._store_document(
            db, row, user_id=ME.id, project_id=None, kind=ArtifactKind.report,
            title="2026 상반기 클라우드 보안 점검 결과", data=REPORT_DATA, summary="",
        )
        row.artifact_id = artifact_id
        await db.commit()
    listed = (await client.get(f"/sessions/{session.id}")).json()
    assert listed["title"] == "2026 상반기 클라우드 보안 점검 결과"

    # The document's own rename carries over.
    renamed = await client.patch(f"/artifacts/{artifact_id}", json={"title": "보안 점검 최종"})
    assert renamed.status_code == 200, renamed.text
    assert (await client.get(f"/sessions/{session.id}")).json()["title"] == "보안 점검 최종"

    # The person names it; the document no longer does.
    assert (await client.patch(f"/sessions/{session.id}", json={"title": "내 점검"})).status_code \
        == 200
    await client.patch(f"/artifacts/{artifact_id}", json={"title": "다른 제목"})
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.get(ChatSession, session.id)
        assert row.title_set_by_user is True
        await sessions_router._store_document(
            db, row, user_id=ME.id, project_id=None, kind=ArtifactKind.report,
            title="다시 쓴 제목", data=REPORT_DATA, summary="",
        )
        await db.commit()
    assert (await client.get(f"/sessions/{session.id}")).json()["title"] == "내 점검"


async def test_a_chat_keeps_its_title_when_it_makes_a_document(env):
    _client, engine, _ = env
    session = ChatSession(user_id=ME.id, kind=SessionKind.chat, title="코드 리뷰 부탁")
    await _add(engine, session)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.get(ChatSession, session.id)
        await sessions_router._store_document(
            db, row, user_id=ME.id, project_id=None, kind=ArtifactKind.html,
            title="리뷰 결과", data={"kind": "html", "content": "<p>x</p>"}, summary="",
        )
        await db.commit()
        assert (await db.get(ChatSession, session.id)).title == "코드 리뷰 부탁"


async def test_someone_elses_document_does_not_rename_my_conversation(env):
    client, engine, who = env
    session = ChatSession(user_id=ME.id, kind=SessionKind.report, title="내 보고서 요청")
    stray = Artifact(user_id=OTHER.id, kind=ArtifactKind.report, title="남의 문서",
                     session_id=session.id, data=REPORT_DATA)
    session.artifact_id = stray.id
    await _add(engine, session, stray)
    who["user"] = OTHER
    await client.patch(f"/artifacts/{stray.id}", json={"title": "바뀐 남의 문서"})
    async with AsyncSession(engine) as db:
        assert (await db.get(ChatSession, session.id)).title == "내 보고서 요청"


# ── the migration ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0059_search_and_document_titles.py"
    spec = importlib.util.spec_from_file_location("search_0059", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_keeps_its_copies_in_step():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0059", "0058")
    assert migration.TITLE_CHARS == chat_service.TITLE_CHARS
    assert migration.SKIP_KEYS == search_text.SKIP_KEYS
    assert migration.TEXT_LIMIT == search_text.TEXT_LIMIT
    for data in (REPORT_DATA, {"slides": [{"title": "표지", "bullets": ["하나", "<b>둘</b>"]}]}):
        assert migration.artifact_text(data) == search_text.artifact_text(data)


def test_the_migration_fills_the_text_and_renames_only_untouched_document_titles():
    migration = _migration()
    first = "사내 클라우드 보안 점검 결과를 정리한 1쪽 보고서. 점검 항목 5개, 조치 계획 포함"
    auto = chat_service.provisional_title(first)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in (
            "CREATE TABLE sessions (id VARCHAR PRIMARY KEY, user_id VARCHAR, kind VARCHAR, "
            "title VARCHAR, artifact_id VARCHAR)",
            "CREATE TABLE messages (id VARCHAR PRIMARY KEY, session_id VARCHAR, role VARCHAR, "
            "content VARCHAR, created_at VARCHAR)",
            "CREATE TABLE artifacts (id VARCHAR PRIMARY KEY, user_id VARCHAR, kind VARCHAR, "
            "title VARCHAR, data JSON)",
        ):
            conn.execute(sa.text(statement))
        sessions = [
            # Still the prompt prefix, with a titled report: renamed.
            ("s1", "u", "report", auto, "a1"),
            # Renamed by hand (differs from the prefix): kept.
            ("s2", "u", "report", "내가 지은 이름", "a2"),
            # A chat: kept.
            ("s3", "u", "chat", auto, "a3"),
            # A deck whose document has no title: kept.
            ("s4", "u", "slides", auto, "a4"),
            # Someone else's artifact id: kept.
            ("s5", "u", "report", auto, "a5"),
        ]
        conn.execute(sa.text("INSERT INTO sessions VALUES (:i, :u, :k, :t, :a)"),
                     [dict(zip("iukta", row, strict=True)) for row in sessions])
        conn.execute(
            sa.text("INSERT INTO messages VALUES (:i, :s, :r, :c, :at)"),
            [
                {"i": f"m{n}", "s": f"s{n}", "r": "user", "c": first, "at": "2026-10-01"}
                for n in range(1, 6)
            ] + [{"i": "m1b", "s": "s1", "r": "user", "c": "두 번째 요청", "at": "2026-10-02"}],
        )
        artifacts = [
            ("a1", "u", "report", "2026 상반기 클라우드 보안 점검 결과", REPORT_DATA),
            ("a2", "u", "report", "문서 제목", REPORT_DATA),
            ("a3", "u", "report", "채팅에서 만든 문서", {"kind": "html", "content": "<p>본문</p>"}),
            ("a4", "u", "deck", "", {"slides": [{"title": "표지"}]}),
            ("a5", "x", "report", "남의 문서", REPORT_DATA),
        ]
        conn.execute(
            sa.text("INSERT INTO artifacts VALUES (:i, :u, :k, :t, :d)"),
            [{"i": i, "u": u, "k": k, "t": t, "d": json.dumps(d)} for i, u, k, t, d in artifacts],
        )
        # The dry run names exactly the rows the upgrade will change.
        assert migration.title_backfill(conn) == [("s1", "2026 상반기 클라우드 보안 점검 결과")]
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        titles = dict(conn.execute(sa.text("SELECT id, title FROM sessions")).all())
        assert titles == {"s1": "2026 상반기 클라우드 보안 점검 결과", "s2": "내가 지은 이름",
                          "s3": auto, "s4": auto, "s5": auto}
        assert set(conn.execute(sa.text("SELECT title_set_by_user FROM sessions")).scalars()) \
            == {0}
        text = dict(conn.execute(sa.text("SELECT id, search_text FROM artifacts")).all())
        assert "방화벽 정책 42건" in text["a1"] and "color" not in text["a1"]
        assert text["a3"] == "본문"
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        columns = {c["name"] for c in sa.inspect(conn).get_columns("artifacts")}
        assert "search_text" not in columns
        assert "title_set_by_user" not in {
            c["name"] for c in sa.inspect(conn).get_columns("sessions")}


# ── 대화 기록 a page at a time ────────────────────────────────────────


async def test_history_pages_count_only_my_listed_conversations(env):
    client, engine, _ = env
    rows = []
    for i in range(120):
        session, messages = _conversation(ME, f"대화 {i}", f"{i}번 이야기", minutes=i)
        rows += [session, *messages]
    for i in range(5):
        session, messages = _conversation(OTHER, f"남의 대화 {i}", "비밀")
        rows += [session, *messages]
    # Empty and old: the sidebar leaves it out, so does every page.
    old = utcnow() - timedelta(days=3)
    rows.append(ChatSession(user_id=ME.id, title="빈 대화", created_at=old, updated_at=utcnow()))
    await _add(engine, *rows)

    first = (await client.get("/sessions/paged", params={"page": 1})).json()
    assert (first["total"], first["page"], first["size"], len(first["items"])) == (120, 1, 50, 50)
    assert [s["title"] for s in first["items"][:3]] == ["대화 0", "대화 1", "대화 2"]
    seen = [s["id"] for s in first["items"]]
    for page in (2, 3):
        body = (await client.get("/sessions/paged", params={"page": page})).json()
        seen += [s["id"] for s in body["items"]]
    assert len(seen) == 120 and len(set(seen)) == 120
    titles = {s["title"] for p in (1, 2, 3) for s in (await client.get(
        "/sessions/paged", params={"page": p})).json()["items"]}
    assert not any(t.startswith("남의") for t in titles) and "빈 대화" not in titles
    # Past the end: the last page, said so.
    past = (await client.get("/sessions/paged", params={"page": 99})).json()
    assert past["page"] == 3 and len(past["items"]) == 20
    assert (await client.get("/sessions/paged", params={"page": 0})).status_code == 422
    assert (await client.get("/sessions/paged", params={"size": 201})).status_code == 422
    small = (await client.get("/sessions/paged", params={"size": 7, "page": 2})).json()
    assert (small["total"], small["page"], len(small["items"])) == (120, 2, 7)


async def test_a_search_can_count_its_conversations(env):
    client, engine, _ = env
    rows = []
    for i in range(7):
        title = "예산 회의" if i < 2 else f"대화 {i}"
        session, messages = _conversation(ME, title, "예산 이야기", "예산 다시", minutes=i)
        rows += [session, *messages]
    theirs, their_msgs = _conversation(OTHER, "예산", "예산")
    await _add(engine, *rows, theirs, *their_msgs)
    page = (await client.get("/search", params={
        "q": "예산", "scope": "sessions", "limit": 3, "offset": 3, "count": "true"})).json()
    assert page["sessionsTotal"] == 7 and len(page["sessions"]) == 3 and page["sessionsMore"]
    assert (await client.get("/search", params={"q": "예산"})).json()["sessionsTotal"] is None
