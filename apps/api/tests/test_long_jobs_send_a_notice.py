"""Long jobs end with a push notice from the replica that ran them: chat turns that wrote an
office document for a while or ran deep research, video clips, studio output, read media
files. A stopped turn or a quick fix sends nothing."""

from __future__ import annotations

import pytest
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.models.chat import ChatSession, Message
from app.models.shelf import StudioItem
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, Job, JobStatus, StoredFile
from app.routers import jobs, sessions
from app.services import push, studio


@pytest.fixture
async def heard(monkeypatch):
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all, tables=[
            metadata.tables[m.__tablename__] for m in (
                User, ChatSession, Message, Artifact, Job)])

    def local():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(core_db, "SessionLocal", local)
    monkeypatch.setattr(jobs, "SessionLocal", local)
    async with local() as db:
        db.add(User(id="me", email="me@example.test", name="나", password_hash="x"))
        db.add(ChatSession(id="s1", user_id="me", title="분기 보고"))
        db.add(Artifact(id="p1", user_id="me", kind=ArtifactKind.pptx, title="발표",
                        session_id="s1"))
        db.add(Job(id="j1", user_id="me", session_id="s1", status=JobStatus.running))
        await db.commit()
    got: list[tuple[str, push.Notice]] = []
    monkeypatch.setattr(push, "notify", lambda user_id, notice: got.append((user_id, notice)))
    yield got
    await engine.dispose()


async def test_an_office_document_written_for_a_while_is_news(heard):
    await sessions._push_answer("s1", force_tool=None, office_made=["p1"], office_target=True,
                                failed=None, elapsed=45)
    [(user_id, notice)] = heard
    assert user_id == "me" and notice.event == "job_done"
    assert notice.title == "문서가 완성됐습니다" and notice.url == "/s/s1?artifact=p1"


async def test_a_quick_fix_or_a_stopped_turn_is_not(heard):
    await sessions._push_answer("s1", force_tool=None, office_made=["p1"], office_target=True,
                                failed=None, elapsed=3)
    await sessions._push_answer("s1", force_tool="kloud__detailed_research", office_made=[],
                                office_target=False, failed="stopped", elapsed=300)
    await sessions._push_answer("s1", force_tool=None, office_made=[], office_target=False,
                                failed=None, elapsed=300)
    assert heard == []


async def test_deep_research_finishing_or_failing_is_news(heard):
    await sessions._push_answer("s1", force_tool="kloud__detailed_research", office_made=[],
                                office_target=False, failed=None, elapsed=2)
    await sessions._push_answer("s1", force_tool="kloud__quick_research", office_made=[],
                                office_target=False, failed="upstream_500", elapsed=90)
    assert [(n.event, n.title) for _, n in heard] == [
        ("job_done", "심층 조사가 끝났습니다"), ("job_failed", "심층 조사를 끝내지 못했습니다")]


async def test_a_failed_video_clip_is_news_once(heard):
    await jobs._finish("j1", status=JobStatus.failed, error="x")
    await jobs._finish("j1", status=JobStatus.failed, error="x")
    assert [(n.event, n.title) for _, n in heard] == [("job_failed", "영상을 만들지 못했습니다")]


def test_studio_output_and_read_media_are_internal(heard):
    studio._notify(StudioItem(id="t1", user_id="me", project_id="pr", kind="briefing",
                              title="요약", state="done", session_id="s9", artifact_id="a9"))
    push.notify_file_read(StoredFile(id="f1", user_id="me", name="회의.m4a", size=1, mime="a",
                                     storage_key="k", project_id="pr"), ok=False)
    (_, made), (_, read) = heard
    assert made.title == "스튜디오 결과물이 완성됐습니다" and made.internal
    assert made.url == "/s/s9?artifact=a9"
    assert read.event == "job_failed" and read.internal and read.url == "/projects/pr"
