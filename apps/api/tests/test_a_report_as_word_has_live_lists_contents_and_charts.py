"""A report written as a Word file has what Word keeps live: numbered lists on a numbering
definition (each list from its own first number), a contents field Word refreshes with the
headings listed as its result, and charts as native, editable charts with their picture beside
them for an editor that draws no chart."""

from __future__ import annotations

import base64
import io
import zipfile

from PIL import Image

from app.models.workspace import ArtifactKind
from app.services import report_export, report_office

CHART = "bar | 억 원\n연도 | 매출\n2025 | 100\n2026 | 120"


def _png() -> str:
    out = io.BytesIO()
    Image.new("RGB", (400, 240), (40, 80, 160)).save(out, "PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def _data() -> dict:
    long = "본문 문장입니다. " * 150
    sections = [{"id": f"s{i}", "heading": f"{i}장 제목", "content": long} for i in range(1, 7)]
    sections[1]["content"] = long + (
        "\n\n1. 첫째 항목\n2. 둘째 항목\n3. 셋째 항목\n\n중간 문단\n\n1. 다시 첫째\n2. 다시 둘째")
    sections[2]["content"] = long + f"\n\n```chart\n{CHART}\n```"
    sections[2]["diagrams"] = {report_export.diagram_key(CHART): _png()}
    return {"sections": sections}


def _xml(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode()


def test_lists_contents_and_charts_stay_live():
    data = report_office.file_bytes(ArtifactKind.docx, "사업 보고", _data())
    body = _xml(data, "word/document.xml")
    numbering = _xml(data, "word/numbering.xml")
    # numbered items carry a numbering, not a typed 「1.」
    assert body.count("<w:numId ") >= 5 and "1. 첫째" not in body
    assert '<w:startOverride w:val="1"/>' in numbering and numbering.count("<w:num ") >= 2
    # the contents: a TOC field whose result is the headings
    assert 'TOC \\o "1-2"' in body and 'w:fldCharType="separate"' in body
    assert "2장 제목" in body
    assert "<w:updateFields" in _xml(data, "word/settings.xml")
    # the chart: native chart part, its picture in front for an editor
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert any(n.startswith("word/charts/chart") for n in archive.namelist())
    assert "mc:AlternateContent" in body and 'Requires="kcpic"' in body
    assert body.index("<a:blip") < body.index("<c:chart")


def test_an_hwp_report_numbers_its_lists_with_hangul_numbering():
    data = report_office.file_bytes(ArtifactKind.hwp, "사업 보고", _data())
    header = _xml(data, "Contents/header.xml")
    section = _xml(data, "Contents/section0.xml")
    # two lists: two numberings, each item on a numbered shape with no typed 「1.」
    assert header.count("<hh:numbering ") == 2 and "^1." in header
    assert header.count('<hh:heading type="NUMBER"') == 2
    assert "<hp:t>첫째 항목</hp:t>" in section and "1. 첫째" not in section
