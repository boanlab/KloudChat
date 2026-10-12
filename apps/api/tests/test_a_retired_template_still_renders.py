"""Retired document 서식: gone from every choice, while documents written in one still draw,
edit and export in the shared seed."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.routers import workspace as ws
from app.schemas.workspace import BlockImage
from app.services import design_templates as dt
from app.services import doc_formats

_BLOCKS = [
    {"layout": "cover", "title": "상반기 점검", "html": "<p>시설팀</p>"},
    {"layout": "section", "title": "현황", "html": "<p>점검은 12건이다.</p>"},
]


def _page(template_id: str) -> Artifact:
    """An HTML document as it was stored when its 서식 was still in the catalogue."""
    seed = dt.rendering(template_id)
    content = dt.render(seed, title="상반기 점검", tokens={}, body=dt.assemble(seed, _BLOCKS))
    return Artifact(
        id="a1",
        user_id="u1",
        kind=ArtifactKind.html,
        title="상반기 점검",
        data={"content": content, "templateId": template_id, "blocks": _BLOCKS},
    )


# ── gone from every choice ─────────────────────────────────────────────


def test_nothing_new_can_choose_a_retired_template():
    shipped = {t.id for t in dt.all_templates()}
    assert not dt.RETIRED & shipped
    assert not any((dt._ROOT / tid).exists() for tid in dt.RETIRED)
    assert not dt.RETIRED & set(doc_formats.TEMPLATE_FORMATS)
    # The shared seed is not a card either.
    assert dt.get(dt.GENERIC_ID) is None and dt.GENERIC_ID not in shipped


@pytest.mark.asyncio
async def test_the_gallery_lists_only_what_is_kept():
    rows = await ws.list_design_templates(user=None, surface="report")
    ids = {row.id for row in rows}
    assert "doc-minutes" in ids and "doc-comparison" in ids
    assert not ids & dt.RETIRED


# ── still drawn ────────────────────────────────────────────────────────


@pytest.mark.parametrize("template_id", sorted(dt.RETIRED))
def test_a_retired_id_renders_in_the_shared_seed(template_id):
    seed = dt.rendering(template_id)
    assert seed is not None and seed.kind == "document" and seed.seed
    html = dt.render(seed, title="제목", tokens={}, body=dt.assemble(seed, _BLOCKS))
    # The shared seed's cover owns its page.
    assert '<div class="cover cover-page"><h1>상반기 점검</h1>' in html
    assert "<section><h2>현황</h2>" in html
    # The shared Word styles, so a .docx still comes out styled.
    assert seed.docx_template.endswith("_document/template.docx")


def test_an_unknown_id_is_still_unknown():
    assert dt.rendering("no-such") is None
    assert dt.rendering(None) is None


@pytest.mark.asyncio
async def test_the_editor_gets_a_stylesheet_for_a_retired_id():
    style = await ws.design_template_style("doc-brief", user=None)
    assert "--paper" in style["css"]
    assert style["wrapBlock"] == "<section><h2>{title}</h2>{body}</section>"
    # A report with no 서식 draws the same seed in the page view.
    assert (await ws.design_template_style(dt.GENERIC_ID, user=None))["css"] == style["css"]


# ── still exported ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["pdf", "docx", "hwpx", "md", "html"])
async def test_a_page_in_a_retired_template_exports(monkeypatch, fmt):
    async def no_printer(html: str) -> None:
        return None

    monkeypatch.setattr(ws.printing, "to_pdf", no_printer)
    artifact = _page("doc-brief")
    assert ws.export_formats(artifact) == ("pdf", "docx", "hwpx", "md")
    reply = await ws._export_document(artifact, fmt)
    assert reply.status_code == 200
    assert len(reply.body) > 0
    if fmt == "pdf":
        assert reply.body.startswith(b"%PDF")
    if fmt in ("docx", "hwpx"):
        assert zipfile.ZipFile(io.BytesIO(reply.body)).testzip() is None


@pytest.mark.asyncio
async def test_a_report_in_a_retired_template_keeps_the_shared_word_styles():
    report = Artifact(
        id="r1",
        user_id="u1",
        kind=ArtifactKind.report,
        title="점검 보고",
        data={
            "templateId": "doc-report",
            "sections": [{"id": "s1", "heading": "현황", "level": 1, "content": "12건."}],
        },
    )
    reply = await ws._export_document(report, "docx")
    styles = zipfile.ZipFile(io.BytesIO(reply.body)).read("word/styles.xml").decode()
    assert "2B4C7E" in styles


# ── still edited ───────────────────────────────────────────────────────


@pytest.fixture
async def db():
    metadata = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
                column.server_default = None
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                metadata.create_all,
                tables=[
                    metadata.tables[model.__tablename__]
                    for model in (User, Artifact, ArtifactVersion)
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_picture_goes_into_a_page_whose_template_was_retired(db, monkeypatch):
    user = User(email="u@example.com", name="U", password_hash="x")
    db.add(user)
    await db.commit()
    page = _page("doc-notice")
    page.id, page.user_id = "p1", user.id
    db.add(page)
    await db.commit()

    async def picture(db, user, artifact_id):
        return "image/png", b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(ws, "_picture_bytes", picture)
    out = await ws.add_block_image(
        "p1", BlockImage(index=1, artifact_id="i1", caption="배치도"), user, db
    )
    content = out.data["content"]
    assert "<figcaption>배치도</figcaption>" in content
    assert out.data["templateId"] == "doc-notice"


# ── the migration ──────────────────────────────────────────────────────


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0055_retire_document_templates.py"
    spec = importlib.util.spec_from_file_location("retire_templates_0055", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_names_the_same_retired_templates():
    assert set(_migration().RETIRED) == dt.RETIRED


def test_the_migration_clears_the_choices_and_keeps_the_rest_on_sqlite():
    migration = _migration()
    assert (migration.revision, migration.down_revision) == ("0055", "0054")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for statement in (
            "CREATE TABLE agents (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)",
            "CREATE TABLE templates (id VARCHAR PRIMARY KEY, render_template_id VARCHAR)",
            "CREATE TABLE projects (id VARCHAR PRIMARY KEY, render_templates JSON)",
            "INSERT INTO agents VALUES ('a1', 'doc-brief'), ('a2', 'doc-minutes')",
            "INSERT INTO templates VALUES ('t1', 'doc-report'), ('t2', 'deck-case')",
        ):
            conn.execute(sa.text(statement))
        conn.execute(
            sa.text("INSERT INTO projects VALUES (:a, :ra), (:b, :rb), ('p3', NULL)"),
            {
                "a": "p1",
                "ra": json.dumps({"report": "doc-notice", "slides": "deck-proposal"}),
                "b": "p2",
                "rb": json.dumps({"report": "doc-minutes"}),
            },
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        agents = dict(conn.execute(sa.text("SELECT id, render_template_id FROM agents")).all())
        assert agents == {"a1": None, "a2": "doc-minutes"}
        kept = dict(conn.execute(sa.text("SELECT id, render_template_id FROM templates")).all())
        assert kept == {"t1": "", "t2": "deck-case"}
        projects = {
            row_id: json.loads(value) if value else None
            for row_id, value in conn.execute(sa.text("SELECT id, render_templates FROM projects"))
        }
        assert projects == {
            "p1": {"slides": "deck-proposal"},
            "p2": {"report": "doc-minutes"},
            "p3": None,
        }
