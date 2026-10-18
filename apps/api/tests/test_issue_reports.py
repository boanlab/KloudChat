"""Issue reports: consented session copy, own-only listing, audited admin status flow."""

from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import deps
from app.core.config import settings
from app.models.chat import ChatSession, Message, PipelineRun, Role, SessionKind
from app.models.issues import IssueReply, IssueReport
from app.models.user import AuditEvent, User, UserRole, utcnow
from app.models.workspace import (
    Agent,
    Artifact,
    ArtifactKind,
    ArtifactVersion,
    Project,
    StoredFile,
    UserPipeline,
)
from app.routers import issues
from app.services import files as file_service
from app.services import issue_snapshot

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
async def engine():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    tables = [metadata.tables[m.__tablename__] for m in (
        User, ChatSession, Message, Artifact, ArtifactVersion, PipelineRun, Agent, Project,
        UserPipeline, StoredFile, AuditEvent, IssueReport, IssueReply)]
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=tables)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def local(engine, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))

    def make():
        return AsyncSession(engine, expire_on_commit=False)

    return make


class _Request:
    headers = {"User-Agent": "tests"}
    client = SimpleNamespace(host="127.0.0.1")


async def _world(local, *, strict: bool = False) -> SimpleNamespace:
    async with local() as db:
        me = User(email="me@example.com", name="나", password_hash="x")
        other = User(email="o@example.com", name="남", password_hash="x")
        admin = User(email="a@example.com", name="관리자", password_hash="x", role=UserRole.admin)
        db.add_all([me, other, admin])
        await db.commit()
        session = ChatSession(user_id=me.id, kind=SessionKind.report, title="견적 비교",
                              model="strict-local/w" if strict else "fixture/base")
        db.add(session)
        await db.commit()
        artifact = Artifact(user_id=me.id, session_id=session.id, kind=ArtifactKind.report,
                            title="견적 비교 보고서", version=2,
                            data={"sections": [{"heading": "개요", "content": "원래 본문"}]})
        db.add(artifact)
        await db.commit()
        db.add(ArtifactVersion(artifact_id=artifact.id, version=1,
                               data={"sections": [{"heading": "개요", "content": "첫 판"}]}))
        question = Message(session_id=session.id, role=Role.user, content="두 견적을 비교해 줘")
        answer = Message(
            session_id=session.id, role=Role.assistant, content="비밀 본문 문장입니다.",
            model=session.model, artifact_ids=[artifact.id],
            routing={"action": "internal_strict_local"} if strict else {"action": "none"},
            created_at=utcnow() + timedelta(seconds=1),
        )
        db.add_all([question, answer])
        run = PipelineRun(session_id=session.id, user_id=me.id, message_id=answer.id,
                          artifact_id=artifact.id, artifact_version=1, pipeline="comparison",
                          flow="write", status="done",
                          stages=[{"stage": "draft", "label": "초안", "status": "done",
                                   "summary": "절 4개", "evidence": {"computed": [
                                       {"name": "합계", "value": "1,200원"}]}},
                                  {"stage": "verify", "label": "검증", "status": "failed",
                                   "reason": "timeout"}])
        db.add(run)
        data = b"col1,col2\n1,2\n"
        made = StoredFile(user_id=me.id, session_id=session.id, name="비교.csv", size=len(data),
                          mime="text/csv")
        made.storage_key = file_service.write_blob(me.id, made.id, made.name, data)
        db.add(made)
        await db.commit()
        return SimpleNamespace(me=me, other=other, admin=admin, session=session,
                               artifact=artifact, question=question, answer=answer, file=made)


def _ask(w, **kw) -> issues.IssueIn:
    fields = {"kind": "bug", "body": "표가 깨집니다", "session_id": w.session.id,
              "client": {"userAgent": "Mozilla/5.0 test", "appBuild": "index-abc.js",
                         "evil": "x" * 5000}}
    return issues.IssueIn(**(fields | kw))


async def _admin_list(db, admin, **kw):
    params = {"kind": None, "status_": None, "since": None, "until": None, "user": None,
              "q": None, "assignee": None, "page": 1, "size": 30} | kw
    return await issues.admin_list(admin, db, **params)


# ── consent ────────────────────────────────────────────────────────────


async def test_unticked_sends_context_but_no_session_content(local):
    w = await _world(local)
    async with local() as db:
        out = await issues.create_issue(_ask(w, message_id=w.answer.id), w.me, db)
        row = await db.get(IssueReport, out["id"])
    assert row.session_shared is False and row.snapshot is None
    assert out["snapshot"] is None
    context = json.dumps(row.context, ensure_ascii=False)
    # Kind, models and stage records travel; no message text, title or document body does.
    assert row.context["session"]["kind"] == "report"
    assert row.context["models"] == ["fixture/base"]
    assert [s["status"] for s in row.context["runs"][0]["stages"]] == ["done", "failed"]
    assert row.context["artifacts"][0]["version"] == 2
    for secret in ("비밀 본문", "두 견적을", "견적 비교", "원래 본문", "1,200원"):
        assert secret not in context
    # Unknown client keys are dropped, known ones kept.
    assert row.context["client"] == {"userAgent": "Mozilla/5.0 test", "appBuild": "index-abc.js"}
    async with local() as db:
        with pytest.raises(HTTPException) as caught:
            await issues.admin_snapshot(row.id, _Request(), w.admin, db)
    assert caught.value.status_code == 404


async def test_ticked_copies_the_session_with_the_pinned_item(local):
    w = await _world(local)
    async with local() as db:
        out = await issues.create_issue(
            _ask(w, share_session=True, message_id=w.answer.id, artifact_id=w.artifact.id),
            w.me, db)
    assert out["sessionShared"] is True
    assert out["snapshot"]["messages"] == 2 and out["snapshot"]["files"] == 1
    async with local() as db:
        snap = await issues.admin_snapshot(out["id"], _Request(), w.admin, db)
    assert [m["content"] for m in snap["messages"]] == [
        "두 견적을 비교해 줘", "비밀 본문 문장입니다."]
    assert snap["pinned"] == {"messageId": w.answer.id, "artifactId": w.artifact.id}
    (artifact,) = snap["artifacts"]
    assert artifact["data"]["sections"][0]["content"] == "원래 본문"
    # The version a run wrote keeps its body; storage keys never reach the screen.
    assert artifact["versions"][0]["data"]["sections"][0]["content"] == "첫 판"
    assert "blob" not in artifact and snap["files"][0]["copied"] is True
    assert "key" not in snap["files"][0]
    assert snap["runs"][0]["stages"][0]["evidence"]["computed"][0]["value"] == "1,200원"


async def test_a_strict_local_session_needs_the_extra_confirmation(local):
    w = await _world(local, strict=True)
    async with local() as db:
        info = await issues.session_info(w.session.id, w.me, db)
        assert info["strictLocal"] is True
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, share_session=True), w.me, db)
        assert caught.value.detail == "internal_data_confirm_required"
        assert (await db.exec(select(IssueReport))).all() == []
        # Unticked needs no confirmation: nothing of the session is copied.
        await issues.create_issue(_ask(w), w.me, db)
        out = await issues.create_issue(_ask(w, share_session=True, confirm_internal=True),
                                        w.me, db)
    assert out["sessionShared"] is True


# ── immutability ───────────────────────────────────────────────────────


async def test_the_snapshot_outlives_edits_and_deletion_of_the_session(local):
    w = await _world(local)
    async with local() as db:
        out = await issues.create_issue(_ask(w, share_session=True), w.me, db)
    async with local() as db:
        answer = await db.get(Message, w.answer.id)
        answer.content = "고친 뒤"
        artifact = await db.get(Artifact, w.artifact.id)
        artifact.data = {"sections": []}
        db.add_all([answer, artifact])
        stored = await db.get(StoredFile, w.file.id)
        file_service.delete_blob(stored.storage_key)
        await db.delete(stored)
        for row in (await db.exec(select(Message))).all():
            await db.delete(row)
        await db.delete(await db.get(ChatSession, w.session.id))
        await db.commit()
    async with local() as db:
        snap = await issues.admin_snapshot(out["id"], _Request(), w.admin, db)
        response = await issues.admin_snapshot_file(out["id"], 0, w.admin, db, artifact=False)
    assert snap["messages"][1]["content"] == "비밀 본문 문장입니다."
    assert snap["artifacts"][0]["data"]["sections"][0]["content"] == "원래 본문"
    assert response.body == b"col1,col2\n1,2\n"


def test_an_oversized_snapshot_is_cut_and_says_what_was_cut():
    pinned = {"id": "p", "content": "중요 " * 30_000, "steps": [{"x": 1}]}
    snapshot = {
        "session": {"artifactId": None},
        "messages": [{"id": f"m{i}", "content": "가" * 50_000, "steps": None}
                     for i in range(10)] + [pinned],
        "runs": [{"stages": [{"evidence": {"text": "x" * 10_000}}]}],
        "artifacts": [{"id": "a", "data": {"x": "y" * 100_000}, "versions": []}],
        "files": [],
        "pinned": {"messageId": "p", "artifactId": None},
        "truncated": [],
    }
    out = issue_snapshot.capped(snapshot, limit=300_000)
    assert issue_snapshot.size_of(out) <= 300_000
    codes = [n["code"] for n in out["truncated"]]
    assert codes[:3] == ["evidence", "versions", "steps"] and "content" in codes
    # The pinned answer stays, clipped less than the rest.
    kept = {m["id"]: m for m in out["messages"]}
    assert "p" in kept and len(kept["p"]["content"]) > 20_000
    assert issue_snapshot.capped({"messages": [], "truncated": []})["truncated"] == []


# ── permissions ────────────────────────────────────────────────────────


async def test_people_see_only_their_own_and_attach_only_their_own(local):
    w = await _world(local)
    async with local() as db:
        out = await issues.create_issue(_ask(w), w.me, db)
        with pytest.raises(HTTPException) as caught:
            await issues.get_issue(out["id"], w.other, db)
        assert caught.value.status_code == 404
        assert await issues.list_issues(w.other, db) == []
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, share_session=True), w.other, db)
        assert caught.value.detail == "session_not_found"
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, message_id="nope"), w.me, db)
        assert caught.value.detail == "message_not_found"
        with pytest.raises(HTTPException) as caught:
            await issues.reply_as_reporter(out["id"], issues.ReplyIn(body="저도요"), w.other, db)
        assert caught.value.status_code == 404
    # Every /admin/issues route depends on AdminUser.
    with pytest.raises(HTTPException) as caught:
        await deps.require_admin(w.me)
    assert caught.value.status_code == 403
    admin_routes = [r for r in issues.router.routes if r.path.startswith("/admin/")]
    assert len(admin_routes) == 7
    for route in admin_routes:
        assert deps.require_admin in set(_calls(route.dependant)), route.path
    for route in issues.router.routes:
        if not route.path.startswith("/admin/"):
            assert deps.require_admin not in set(_calls(route.dependant)), route.path


def _calls(dependant):
    yield dependant.call
    for child in dependant.dependencies:
        yield from _calls(child)


async def test_reports_are_rate_limited_per_person(local):
    w = await _world(local)
    async with local() as db:
        for _ in range(issues.RATE_REPORTS):
            await issues.create_issue(_ask(w, session_id=None), w.me, db)
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, session_id=None), w.me, db)
        assert caught.value.status_code == 429
        # Someone else is not held back by it.
        await issues.create_issue(_ask(w, session_id=None), w.other, db)


async def test_a_screenshot_must_be_a_picture_and_moves_into_the_report(local):
    w = await _world(local)
    async with local() as db:
        text = StoredFile(user_id=w.me.id, name="shot.png", mime="image/png")
        text.storage_key = file_service.write_blob(w.me.id, text.id, text.name, b"<html>x</html>")
        shot = StoredFile(user_id=w.me.id, name="shot.png", mime="image/png", size=len(PNG))
        shot.storage_key = file_service.write_blob(w.me.id, shot.id, shot.name, PNG)
        db.add_all([text, shot])
        await db.commit()
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, screenshot_file_id=text.id), w.me, db)
        assert caught.value.detail == "screenshot_not_image"
        with pytest.raises(HTTPException) as caught:
            await issues.create_issue(_ask(w, screenshot_file_id=shot.id), w.other, db)
        assert caught.value.status_code == 404
        out = await issues.create_issue(_ask(w, screenshot_file_id=shot.id), w.me, db)
        assert out["hasScreenshot"] is True
        assert await db.get(StoredFile, shot.id) is None
        response = await issues.admin_screenshot(out["id"], w.admin, db)
        assert response.body == PNG and response.media_type == "image/png"


# ── the administrator's side ───────────────────────────────────────────


async def test_the_status_flow_replies_and_the_reply_badge(local):
    w = await _world(local)
    async with local() as db:
        out = await issues.create_issue(_ask(w), w.me, db)
        rid = out["id"]
        assert (await issues.unread_issues(w.me, db))["count"] == 0
        with pytest.raises(HTTPException) as caught:
            await issues.admin_update(rid, issues.IssuePatch(status="on_hold"), _Request(),
                                      w.admin, db)
        assert caught.value.detail == "hold_reason_required"
        detail = await issues.admin_update(
            rid, issues.IssuePatch(status="checking", assignee_id=w.admin.id), _Request(),
            w.admin, db)
        assert detail["status"] == "checking" and detail["assignee"]["id"] == w.admin.id
        with pytest.raises(HTTPException) as caught:
            await issues.admin_update(rid, issues.IssuePatch(assignee_id=w.me.id), _Request(),
                                      w.admin, db)
        assert caught.value.detail == "assignee_not_admin"
        await issues.admin_update(
            rid, issues.IssuePatch(status="on_hold", hold_reason="다음 배포에서 고칩니다"),
            _Request(), w.admin, db)
        await issues.reply_as_admin(rid, issues.ReplyIn(body="재현했습니다"), w.admin, db)
        assert (await issues.unread_issues(w.me, db))["count"] == 1
        mine = await issues.get_issue(rid, w.me, db)
        assert mine["status"] == "on_hold" and mine["holdReason"] == "다음 배포에서 고칩니다"
        assert [(r["status"], r["body"]) for r in mine["thread"]] == [
            ("checking", ""), ("on_hold", "다음 배포에서 고칩니다"), (None, "재현했습니다")]
        await issues.read_issue(rid, w.me, db)
        assert (await issues.unread_issues(w.me, db))["count"] == 0
        await issues.admin_update(rid, issues.IssuePatch(status="resolved"), _Request(),
                                  w.admin, db)
        with pytest.raises(HTTPException) as caught:
            await issues.admin_update(rid, issues.IssuePatch(status="on_hold", hold_reason="x"),
                                      _Request(), w.admin, db)
        assert caught.value.detail == "issue_transition_refused"
        reopened = await issues.admin_update(rid, issues.IssuePatch(status="checking"),
                                             _Request(), w.admin, db)
        assert reopened["holdReason"] == "" and reopened["transitions"] == ["on_hold", "resolved"]
        audits = (await db.exec(select(AuditEvent))).all()
    assert {a.action for a in audits} == {"issue.update"}
    assert audits[0].event_metadata["changes"]["status"] == ["received", "checking"]


async def test_the_list_filters_and_every_snapshot_open_is_audited(local):
    w = await _world(local)
    async with local() as db:
        shared = await issues.create_issue(_ask(w, share_session=True, body="표가 깨짐"),
                                           w.me, db)
        await issues.create_issue(_ask(w, kind="feature", body="다크 모드 100%"), w.me, db)
        await issues.create_issue(_ask(w, session_id=None, body="기타 문의"), w.other, db)
        everything = await _admin_list(db, w.admin)
        assert everything["total"] == 3 and everything["counts"]["received"] == 3
        assert everything["admins"] == [{"id": w.admin.id, "name": "관리자"}]
        assert (await _admin_list(db, w.admin, kind="feature"))["total"] == 1
        # LIKE wildcards are matched literally.
        assert (await _admin_list(db, w.admin, q="100%"))["total"] == 1
        assert (await _admin_list(db, w.admin, q="%"))["total"] == 1
        assert (await _admin_list(db, w.admin, user="o@example"))["total"] == 1
        assert (await _admin_list(db, w.admin, assignee="none"))["total"] == 3
        assert (await _admin_list(db, w.admin, status_="resolved"))["total"] == 0
        row = (await _admin_list(db, w.admin, q="표가"))["items"][0]
        assert row["reporter"]["email"] == "me@example.com" and row["snapshot"]["messages"] == 2
        await issues.admin_snapshot(shared["id"], _Request(), w.admin, db)
        await issues.admin_snapshot(shared["id"], _Request(), w.admin, db)
        opened = (await db.exec(select(AuditEvent))).all()
    assert [a.action for a in opened] == ["issue.snapshot_view"] * 2
    assert opened[0].actor_id == w.admin.id and opened[0].event_metadata["issueId"] == shared["id"]


def test_the_audit_codes_are_known():
    from app.services import audit as audit_service

    assert {"issue.update", "issue.snapshot_view"} <= audit_service.ACTIONS


# ── the migration ──────────────────────────────────────────────────────


def test_the_migration_adds_and_drops_issue_tables_on_sqlite():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0057_issue_reports.py"
    spec = importlib.util.spec_from_file_location("issue_reports_0057", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0057"
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        inspector = sa.inspect(conn)
        assert {c["name"] for c in inspector.get_columns("issue_reports")} == {
            c.name for c in IssueReport.__table__.columns}
        assert {c["name"] for c in inspector.get_columns("issue_replies")} == {
            c.name for c in IssueReply.__table__.columns}
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
        assert not {"issue_reports", "issue_replies"} & set(sa.inspect(conn).get_table_names())
