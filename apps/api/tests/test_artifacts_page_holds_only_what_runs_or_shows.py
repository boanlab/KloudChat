"""The 아티팩트 page lists HTML pages, code files, charts and diagrams; reports and decks live in
파일함 › 작업 문서, generated pictures, sound and video in 만든 파일 by their files, and an
answer's inline code block is part of the answer, never an artifact."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Agent, Artifact, ArtifactKind, StoredFile
from app.routers import sessions
from app.routers import workspace as ws
from app.services import artifact_extract
from app.services.artifact_section import section_of

_PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0092_artifact_sections.py"
_SPEC = importlib.util.spec_from_file_location("m0092", _PATH)
m0092 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m0092)


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


ME = User(id="u1", email="u1@example.test", password_hash="x", name="U")

FIGURE = {"kind": "image", "style": "figure", "figure": "flow", "source": "graph TD; A-->B",
          "src": "/api/files/" + "f" * 32 + "/content"}
PICTURE = {"kind": "image", "style": "photo", "prompt": "고양이", "src": "/api/files/x/content"}
CHART_PNG = {"kind": "image", "style": "차트", "engine": "matplotlib",
             "src": "/api/files/y/content"}


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: SQLModel.metadata.create_all(
            c, tables=[Artifact.__table__, StoredFile.__table__, Agent.__table__]))
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sessions, "SessionLocal", made)
    yield made
    await engine.dispose()


async def _seed(maker) -> None:
    rows = [
        ("page", ArtifactKind.html, "계산기", {"kind": "html", "content": "<p>x</p>"}),
        ("file", ArtifactKind.code, "column_mean.py", {"kind": "code", "content": "x = 1"}),
        ("chart", ArtifactKind.chart, "월별 추이", {"kind": "chart", "series": []}),
        ("png", ArtifactKind.image, "막대 그래프", CHART_PNG),
        ("fig", ArtifactKind.image, "흐름도", FIGURE),
        ("pic", ArtifactKind.image, "고양이", PICTURE),
        ("song", ArtifactKind.audio, "노래", {"kind": "audio"}),
        ("rep", ArtifactKind.report, "주간 보고", {"kind": "report", "sections": []}),
        ("ppt", ArtifactKind.pptx, "발표", {"kind": "pptx"}),
    ]
    async with maker() as db:
        for row_id, kind, title, data in rows:
            db.add(Artifact(id=row_id, user_id="u1", kind=kind, title=title, data=data))
        # an old inline block, as the migration marks it
        db.add(Artifact(id="old", user_id="u1", kind=ArtifactKind.code, title="sql 2",
                        data={"content": "select 1", "language": "sql"}, section="snippet"))
        await db.commit()


def test_each_kind_has_its_place() -> None:
    assert section_of("html", {"kind": "html"}) == "html"
    assert section_of(ArtifactKind.code, {"content": "x"}) == "code"
    assert section_of("chart", {}) == "chart"
    assert section_of("image", FIGURE) == "diagram"
    assert section_of("image", CHART_PNG) == "chart"
    assert section_of("image", PICTURE) == "media"
    assert section_of("video", {}) == "media"
    for kind in ("report", "deck", "hwp", "docx", "pptx"):
        assert section_of(kind, {}) == "document"


async def _gallery(db, section=None, q=None, sort="updated", dir="desc", cursor=None, limit=50):
    return await ws.page_gallery(ME, db, section=section, q=q, sort=sort, dir=dir,
                                 cursor=cursor, limit=limit)


async def test_the_gallery_lists_pages_code_charts_and_diagrams_only(maker) -> None:
    await _seed(maker)
    async with maker() as db:
        async def ids(section=None, q=None):
            return sorted(r.id for r in (await _gallery(db, section, q)).items)

        assert await ids() == ["chart", "fig", "file", "page", "png"]
        assert await ids("chart") == ["chart", "png"]
        assert await ids("diagram") == ["fig"]
        assert await ids("code,html") == ["file", "page"]
        # a kind that is not a section of the page is nothing, not everything
        assert await ids("report") == []
        assert await ids(q="흐름") == ["fig"]
        page = await _gallery(db)
        assert page.counts == {"html": 1, "code": 1, "chart": 2, "diagram": 1}
        assert page.total == 5
        assert {row.section for row in page.items} == {"html", "code", "chart", "diagram"}
        searched = await _gallery(db, "chart", q="흐름")
        assert searched.total == 0 and searched.counts["diagram"] == 1

        # sorted by a column, a page at a time, every row once
        first = await _gallery(db, sort="title", dir="asc", limit=2)
        second = await _gallery(db, sort="title", dir="asc", cursor=first.next, limit=10)
        titles = [r.title for r in first.items + second.items]
        assert titles == sorted(titles) and len(titles) == 5 and second.next is None

        # the plain list (pictures for a slide, a project's results) keeps every kind but the
        # old inline block
        rows = await ws.list_artifacts(ME, db, kind=None, project_id=None, q=None, limit=60,
                                       before_at=None, before_id=None)
        assert "old" not in {r.id for r in rows} and {"pic", "rep"} <= {r.id for r in rows}
        plain = await ws.artifact_counts(ME, db, q=None)
        assert plain["counts"].get("code") == 1


async def test_an_edited_snippet_stays_unlisted(maker) -> None:
    await _seed(maker)
    async with maker() as db:
        old = await db.get(Artifact, "old")
        old.data = {"kind": "code", "content": "select 2", "language": "sql"}
        db.add(old)
        await db.commit()
        assert (await db.get(Artifact, "old")).section == "snippet"


async def test_an_inline_code_block_is_not_stored_and_a_made_file_is(maker) -> None:
    block = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(20)) + "\n```"
    answer = f"## 풀이\n\n{block}\n\n위 코드를 실행하세요."
    assert not hasattr(artifact_extract, "extract")
    made = await sessions._store_artifacts(
        user_id="u1", session_id="s1", project_id="", content=answer)
    assert made is None
    async with maker() as db:
        assert (await db.exec(select(Artifact))).all() == []

    request = {"kind": "code", "title": "mean.py",
               "data": {"kind": "code", "content": "print(1)", "language": "python"}}
    made = await sessions._store_artifacts(
        user_id="u1", session_id="s1", project_id="", content=answer,
        requested_artifacts=[request])
    async with maker() as db:
        stored = (await db.exec(select(Artifact))).all()
    assert [(a.id, a.section) for a in stored] == [(made, "code")]


async def test_the_file_box_leaves_out_copies_of_artifacts(maker) -> None:
    async with maker() as db:
        db.add(StoredFile(id="up", user_id="u1", name="강의.pdf", origin="upload"))
        db.add(StoredFile(id="pic", user_id="u1", name="고양이.png", origin="generated"))
        db.add(StoredFile(id="exp", user_id="u1", name="주간 보고.pdf", origin="generated",
                          copy_of="rep"))
        db.add(StoredFile(id="figpng", user_id="u1", name="흐름도.png", origin="generated",
                          copy_of="fig"))
        await db.commit()
        page = await ws.page_files(ME, db, box=True, origin="generated", q=None,
                                   project_id=None, session_id=None, sort="created",
                                   dir="desc", cursor=None, limit=50)
        assert [row.id for row in page.items] == ["pic"]
        assert page.counts["generated"] == 1 and page.counts["upload"] == 1
        uploads = await ws.page_files(ME, db, box=True, origin="upload", q=None,
                                      project_id=None, session_id=None, sort="created",
                                      dir="desc", cursor=None, limit=50)
        assert [row.id for row in uploads.items] == ["up"]
        # the conversation's own file list still has the export
        listed = {row.id for row in await ws.list_files(ME, db)}
        assert {"exp", "figpng"} <= listed


_MIGRATION_DDL = (
    """CREATE TABLE artifacts (id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT,
        kind TEXT, title TEXT, data TEXT)""",
    """CREATE TABLE files (id TEXT PRIMARY KEY, name TEXT, origin TEXT, session_id TEXT)""",
)


def test_the_migration_hides_inline_blocks_and_marks_copies() -> None:
    engine = sa.create_engine("sqlite://")
    fig_file = "f" * 32
    artifacts = [
        ("snip", "s1", "code", "sql 2", {"content": "select 1", "language": "sql"}),
        ("snip2", "s1", "html", "Fixed version:", {"content": "<p>x</p>", "language": "html"}),
        ("made", "s1", "code", "column_mean.py",
         {"kind": "code", "content": "x", "language": "python"}),
        ("page", "s1", "html", "비교", {"kind": "html", "content": "<p>x</p>",
                                       "templateId": "t"}),
        ("fig", "s1", "image", "흐름도", {**FIGURE, "src": f"/api/files/{fig_file}/content"}),
        ("pic", "s1", "image", "고양이", PICTURE),
        ("rep", "s2", "report", "한빛물류 제안서", {"sections": []}),
    ]
    files = [
        (fig_file, "흐름도.png", "generated", "s1"),
        ("exp", "한빛물류 제안서.pdf", "generated", "s2"),
        ("run", "결과.docx", "generated", "s2"),
        ("up", "한빛물류 제안서.pdf", "upload", "s2"),
    ]
    with engine.begin() as connection:
        for statement in _MIGRATION_DDL:
            connection.exec_driver_sql(statement)
        for row_id, session_id, kind, title, data in artifacts:
            connection.execute(sa.text(
                "INSERT INTO artifacts VALUES (:id, 'u1', :s, :kind, :title, :data)"),
                {"id": row_id, "s": session_id, "kind": kind, "title": title,
                 "data": json.dumps(data)})
        for row in files:
            connection.execute(sa.text("INSERT INTO files VALUES (:a, :b, :c, :d)"),
                               dict(zip("abcd", row, strict=True)))
        with Operations.context(MigrationContext.configure(connection)):
            m0092.upgrade()
        sections = dict(connection.execute(sa.text("SELECT id, section FROM artifacts")).all())
        copies = dict(connection.execute(sa.text("SELECT id, copy_of FROM files")).all())

    assert sections == {"snip": "snippet", "snip2": "snippet", "made": "code", "page": "html",
                        "fig": "diagram", "pic": "media", "rep": "document"}
    assert copies == {fig_file: "fig", "exp": "rep", "run": None, "up": None}
