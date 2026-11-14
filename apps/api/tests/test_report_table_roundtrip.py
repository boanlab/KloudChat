"""Real table save, read and export routes over an in-memory workspace.

A table an older client saved as HTML is stored as a Markdown table (cell merges have no
Markdown and are flattened); the exporters' grid matching is still checked with grids read
from HTML directly."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind, ArtifactVersion
from app.routers import workspace
from app.services import report_export, richtext


def _converted(markup: str) -> dict:
    """A section with the grids of its HTML, as the exporters accept them."""
    return {
        "content": richtext.to_markdown(markup),
        "format": "markdown",
        "tables": richtext.grids(markup),
    }

FIXTURES = Path(__file__).parent / "fixtures/report-table"
_DDL = (
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, project_id TEXT,
        kind TEXT, title TEXT, data TEXT, storage_key TEXT, version INTEGER,
        created_at DATETIME, updated_at DATETIME,
        search_text TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE artifact_versions (
        id TEXT PRIMARY KEY, artifact_id TEXT, version INTEGER, data TEXT,
        storage_key TEXT, summary TEXT, created_at DATETIME
    )""",
)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"


@pytest.fixture
async def stored_table(request):
    data = json.loads((FIXTURES / f"report-table-{request.param}.json").read_text())
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    user = User(id="table-user", email="table@example.test", password_hash="unused", name="Table")
    original = {"kind": "report", "sources": [], "sections": []}
    async with AsyncSession(engine, expire_on_commit=False) as db:
        artifact = Artifact(user_id=user.id, kind=ArtifactKind.report, title="Table", data=original)
        db.add(artifact)
        await db.commit()
        artifact_id = artifact.id
    app = FastAPI()
    app.include_router(workspace.router)

    async def session():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            saved = await client.patch(
                f"/artifacts/{artifact_id}",
                json={
                    "title": data["title"],
                    "data": data,
                    "expectedVersion": 1,
                    "summary": "table edit",
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == 2
            loaded = await client.get(f"/artifacts/{artifact_id}")
            assert loaded.status_code == 200
            assert loaded.json() == saved.json()
            section = loaded.json()["data"]["sections"][0]
            assert section["format"] == "markdown"
            rows = [line for line in section["content"].splitlines() if line.startswith("|")]
            # header, rule, body rows; a row a merge covered whole is gone
            assert len(rows) == (3 if request.param == "full-width-merged" else 4)
            for text in ("Item", "Value", "Owner", "Alpha", "10", "A", "Beta", "20", "B"):
                assert text in section["content"]
            expected_span = {"merged": 2, "full-width-merged": 3}.get(request.param)
            # A fresh ORM session checks the committed previous version, not an in-memory object.
            async with AsyncSession(engine) as db:
                history = (await db.exec(select(ArtifactVersion))).all()
                assert len(history) == 1
                assert history[0].version == 1
                assert history[0].data == original
            yield client, artifact_id, expected_span
    finally:
        await engine.dispose()


@pytest.mark.parametrize("stored_table", ["merged", "split", "full-width-merged"], indirect=True)
@pytest.mark.parametrize("format", ["docx", "hwpx", "pdf"])
async def test_saved_browser_table_keeps_its_cells_in_exports(stored_table, format):
    client, artifact_id, _ = stored_table
    response = await client.get(f"/artifacts/{artifact_id}/export?format={format}")
    assert response.status_code == 200
    if format == "docx":
        with ZipFile(BytesIO(response.content)) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        table = document.find(f".//{W}tbl")
        assert table is not None
        # Markdown has no merges: every cell stands alone
        assert table.findall(f".//{W}gridSpan") == []
        text = "\n".join(node.text or "" for node in table.iter(f"{W}t"))
    elif format == "hwpx":
        with ZipFile(BytesIO(response.content)) as archive:
            document = ET.fromstring(archive.read("Contents/section0.xml"))
        table = document.find(f".//{HP}tbl")
        assert table is not None
        spans = [node.attrib for node in table.iter(f"{HP}cellSpan")]
        assert all(span == {"colSpan": "1", "rowSpan": "1"} for span in spans)
        text = "\n".join(node.text or "" for node in table.iter(f"{HP}t"))
    else:
        assert response.content.startswith(b"%PDF-")
        reader = PdfReader(BytesIO(response.content))
        text = "\n".join(page.extract_text() for page in reader.pages)
    for expected in ("Item", "Value", "Owner", "Alpha", "10", "A", "Beta", "20", "B"):
        assert expected in text


@pytest.mark.parametrize(
    "markup, expected_rows",
    [
        ("<tr><td></td><td></td></tr><tr><td>A</td><td>B</td></tr>", 2),
        ("<tr><td>A</td><td>B</td></tr><tr><td></td><td></td></tr>", 2),
        (
            "<tr><td>A</td><td>B</td></tr><tr><td></td><td></td></tr><tr><td>C</td><td>D</td></tr>",
            3,
        ),
        ("<tr><td rowspan='2' colspan='2'>A</td></tr><tr></tr><tr><td>B</td><td>C</td></tr>", 3),
        ("<tr><td rowspan='3'>A</td><td>B</td></tr><tr><td></td></tr><tr><td>C</td></tr>", 3),
    ],
)
@pytest.mark.parametrize("format", ["docx", "hwpx", "pdf"])
def test_structural_empty_rows_match_the_original_html_grid(
    markup, expected_rows, format, monkeypatch
):
    sections = [_converted(f"<table>{markup}</table>")]
    section = sections[0]
    original = section["tables"][0]
    assert len(original.rows) == expected_rows
    tables = [
        payload
        for kind, payload, *_ in report_export._markdown_to_lines(
            section["content"], section["tables"]
        )
        if kind == "table"
    ]
    assert tables == [original]
    assert report_export._export_grid(tables[0]).rows == original.rows
    if format == "docx":
        with ZipFile(BytesIO(report_export.to_docx("Rows", sections))) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        table = document.find(f".//{W}tbl")
        assert len(table.findall(f"{W}tr")) == expected_rows
    elif format == "hwpx":
        with ZipFile(BytesIO(report_export.to_hwpx("Rows", sections))) as archive:
            document = ET.fromstring(archive.read("Contents/section0.xml"))
        table = document.find(f".//{HP}tbl")
        assert int(table.get("rowCnt")) == expected_rows
    else:
        drawn = []
        real_pdf_table = report_export._pdf_table

        def observe(*args, **kwargs):
            table = real_pdf_table(*args, **kwargs)
            drawn.append(table)
            return table

        monkeypatch.setattr(report_export, "_pdf_table", observe)
        body = report_export.to_pdf("Rows", sections)
        assert PdfReader(BytesIO(body)).pages
        assert len(drawn) == 1
        assert drawn[0]._nrows == expected_rows


def test_plain_markdown_keeps_its_existing_empty_row_cleanup():
    source = "| A | B |\n|---|---|\n| C | D |\n|  |  |\n| E | F |"
    tables = [
        payload for kind, payload, *_ in report_export._markdown_to_lines(source) if kind == "table"
    ]
    assert [table.flat() for table in tables] == [[["A", "B"]], [["C", "D"], ["E", "F"]]]
    assert report_export._export_grid([["A", "B"], ["", ""], ["C", "D"]]).flat() == [
        ["A", "B"],
        ["C", "D"],
    ]


@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize("separator", ["|  |  |", "| : | : |", "|  |  |\n|---|---|"])
def test_unrelated_html_grid_does_not_change_markdown_table_boundaries(position, separator):
    markup = "<table><tr><td>Unrelated</td><td>Grid</td></tr></table>"
    source = f"| A | B |\n|---|---|\n| C | D |\n{separator}\n| E | F |"
    paragraphs = "".join(f"<p>{line}</p>" for line in source.splitlines())
    pieces = [markup, "<p>Separate.</p>", paragraphs]
    if position == "after":
        pieces.reverse()
    section = _converted("".join(pieces))
    actual = report_export._markdown_to_lines(section["content"], section["tables"])
    expected = report_export._markdown_to_lines(section["content"])
    assert actual == expected


@pytest.mark.parametrize("separator", ["|  |  |", "| : | : |", "|  |  |\n|---|---|"])
def test_unmatched_grid_prefix_does_not_change_markdown_table_boundaries(separator):
    source = f"| A | B |\n|---|---|\n| C | D |\n{separator}\n| E | F |"
    grid = richtext.Grid(rows=[[richtext.Cell("A"), richtext.Cell("B")]])
    assert report_export._markdown_to_lines(source, [grid]) == report_export._markdown_to_lines(
        source
    )


@pytest.mark.parametrize("format", ["docx", "hwpx", "pdf"])
def test_mixed_html_and_markdown_exports_keep_the_original_plain_table_boundaries(format):
    markup = (
        "<table><tr><td>Unrelated</td><td>Grid</td></tr></table><p>Separate.</p>"
        "<p>| A | B |</p><p>|---|---|</p><p>| C | D |</p><p>|  |  |</p><p>| E | F |</p>"
    )
    section = _converted(markup)
    plain = {key: value for key, value in section.items() if key != "tables"}
    export = getattr(report_export, f"to_{format}")
    actual, expected = export("Mixed tables", [section]), export("Mixed tables", [plain])
    if format == "pdf":
        actual_pages = PdfReader(BytesIO(actual)).pages
        expected_pages = PdfReader(BytesIO(expected)).pages
        assert [page.get_contents().get_data() for page in actual_pages] == [
            page.get_contents().get_data() for page in expected_pages
        ]
    else:
        member = "word/document.xml" if format == "docx" else "Contents/section0.xml"
        with ZipFile(BytesIO(actual)) as actual_zip, ZipFile(BytesIO(expected)) as expected_zip:
            assert actual_zip.read(member) == expected_zip.read(member)


@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize("separated", [False, True])
def test_only_the_matched_table_keeps_structural_empty_rows(position, separated):
    markup = "<table><tr><td rowspan='2' colspan='2'>Merged</td></tr><tr></tr></table>"
    source = "| A | B |\n|---|---|\n| C | D |\n|  |  |\n| E | F |"
    paragraphs = "".join(f"<p>{line}</p>" for line in source.splitlines())
    pieces = [markup, "<p>Separate.</p>" if separated else "", paragraphs]
    if position == "after":
        pieces.reverse()
    section = _converted("".join(pieces))
    tables = [
        payload
        for kind, payload, *_ in report_export._markdown_to_lines(
            section["content"], section["tables"]
        )
        if kind == "table"
    ]
    expected = [
        payload for kind, payload, *_ in report_export._markdown_to_lines(source) if kind == "table"
    ]
    expected.insert(0 if position == "before" else len(expected), section["tables"][0])
    assert tables == expected


def test_adjacent_structural_grids_keep_their_own_empty_rows():
    markup = (
        "<table><tr><td rowspan='2' colspan='2'>First</td></tr><tr></tr></table>"
        "<table><tr><td>Second</td><td>Table</td></tr><tr><td></td><td></td></tr></table>"
    )
    section = _converted(markup)
    tables = [
        payload
        for kind, payload, *_ in report_export._markdown_to_lines(
            section["content"], section["tables"]
        )
        if kind == "table"
    ]
    assert tables == section["tables"]


def test_an_entirely_empty_html_table_still_emits_no_export_table():
    sections = [_converted("<table><tr><td></td></tr></table>")]
    assert sections[0]["content"] == ""
    assert report_export._export_grid(sections[0]["tables"][0]).rows == []
