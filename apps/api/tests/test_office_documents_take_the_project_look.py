"""HWP and Word documents KloudChat writes take the project's theme and the report template:
a report as HWP follows its template as HWPX can (margins, body face, heading colour), and the
office tools' new documents and blank ones take the theme and template too."""

from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document

from app.models.workspace import ArtifactKind
from app.services import docx_doc, hwp_doc, office_look, report_office
from app.services.tools import hwp as hwp_tools
from app.services.tools.base import ToolContext

TEMPLATE = "doc-result-report"


def _header(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("Contents/header.xml").decode()


def _section(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("Contents/section0.xml").decode()


def test_the_template_reaches_the_hwp_report():
    look = office_look.Look(template_id=TEMPLATE)
    tokens, page = office_look.hwpx(look)
    margins, family, _ = office_look._from_word(look.template.docx_template)
    assert page and page["margins"] == dict(margins)
    data = {"sections": [{"id": "a", "heading": "개요", "content": "본문"}],
            "templateId": TEMPLATE}
    with_template = report_office.file_bytes(ArtifactKind.hwp, "보고", data)
    without = report_office.file_bytes(ArtifactKind.hwp, "보고", {**data, "templateId": ""})
    assert _section(with_template) != _section(without) or \
        _header(with_template) != _header(without)
    # the theme's colour wins over the template's
    tokens, _ = office_look.hwpx(office_look.Look(tokens={"accent": "#123456"},
                                                  template_id=TEMPLATE))
    assert tokens["accent"] == "#123456"


def test_a_written_document_takes_the_theme_and_the_template():
    look = office_look.Look(tokens={"accent": "#AA2233", "font": "serif"}, template_id=TEMPLATE)
    plain = hwp_doc.official("공문", "1. 관련: 가", look=None)
    themed = hwp_doc.official("공문", "1. 관련: 가", look=look)
    assert "#AA2233" in _header(themed) and "#AA2233" not in _header(plain)
    word = docx_doc.official("공문", "1. 관련: 가", look=look)
    base = Document(look.template.docx_template)
    made = Document(io.BytesIO(word))
    # the template's own styles are the document's base
    assert {s.name for s in base.styles} <= {s.name for s in made.styles}
    blank = docx_doc.blank(look)
    assert Document(io.BytesIO(blank)).paragraphs


@pytest.mark.asyncio
async def test_the_hwp_tool_writes_in_the_conversation_look(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession
    from test_report_table_roundtrip import _DDL

    from app.core.config import settings

    monkeypatch.setattr(settings, "file_storage_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        for statement in _DDL:
            await connection.exec_driver_sql(statement)
    monkeypatch.setattr(hwp_tools, "SessionLocal",
                        lambda: AsyncSession(engine, expire_on_commit=False))
    ctx = ToolContext(user_id="u", session_id="s",
                      look={"tokens": {"accent": "#AA2233"}, "templateId": TEMPLATE})
    made = await hwp_tools.hwp_write({"title": "공문", "format": "official", "body": "1. 가"},
                                     ctx)
    assert not made.failed
    from app.models.workspace import Artifact
    from app.services import office_doc

    async with AsyncSession(engine) as db:
        artifact = await db.get(Artifact, made.trace["artifactId"])
        assert "#AA2233" in _header(office_doc.read(artifact))
    await engine.dispose()
