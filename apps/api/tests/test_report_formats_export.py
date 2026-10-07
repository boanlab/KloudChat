"""A document by purpose leaves with its head and its heading numbers in every file."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from pypdf import PdfReader

from app.services import doc_formats, report_export

SECTIONS = [
    {
        "id": "s1",
        "heading": "서론",
        "level": 1,
        "content": "본문 한 문단.\n\n### 배경\n\n내용.\n\n### 목적\n\n내용.",
    },
    {"id": "s2", "heading": "결론", "level": 1, "content": "마무리."},
]

#: (format id, request the values come from, the model's answer)
CASES = {
    "cover": (
        "term",
        "회로이론 기말 리포트, 담당교수 김교수, 제출일 2026-10-02",
        {"fields": {"과목": "회로이론", "담당교수": "김교수", "제출일": "2026-10-02"}},
    ),
    "header": (
        "incident",
        "장애 보고서. 심각도 SEV2, 작성자 박운영",
        {"fields": {"심각도": "SEV2", "작성자": "박운영"}},
    ),
    "memo": (
        "official",
        "공문. 수신 각 학과장, 발신 교무처",
        {"fields": {"수신": "각 학과장", "발신": "교무처"}},
    ),
    "press": (
        "press",
        "보도자료. 배포일 2026-10-04, 문의처 홍보팀",
        {"fields": {"배포일": "2026-10-04", "문의처": "홍보팀"}, "subtitle": "부제"},
    ),
    "paper": (
        "paper",
        "논문 초안. 저자 이연구, 소속 단국대학교",
        {"fields": {"저자": "이연구", "소속": "단국대학교"}, "keywords": ["검색", "요약"]},
    ),
}

#: The headings each numbering style writes for SECTIONS.
NUMBERED = {
    "decimal": ["1. 서론", "1.1 배경", "1.2 목적", "2. 결론"],
    "roman": ["I. 서론", "A. 배경", "B. 목적", "II. 결론"],
    "korean": ["1. 서론", "가. 배경", "나. 목적", "2. 결론"],
    "official": ["1. 서론", "가. 배경", "나. 목적", "2. 결론"],
}


def _block(head: str) -> dict:
    format_id, request, answer = CASES[head]
    block = doc_formats.title_block(doc_formats.BY_ID[format_id], answer, request)
    assert block["head"] == head
    return block


def _text(kind: str, title: str, sections: list[dict], block: dict | None) -> str:
    """The exported file's text, whitespace folded."""
    if kind == "pdf":
        data = report_export.to_pdf(title, sections, title_block=block)
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    else:
        writer = report_export.to_docx if kind == "docx" else report_export.to_hwpx
        part = "word/document.xml" if kind == "docx" else "Contents/section0.xml"
        archive = zipfile.ZipFile(io.BytesIO(writer(title, sections, title_block=block)))
        xml = archive.read(part).decode()
        ElementTree.fromstring(xml)  # well-formed, or Word and Hancom refuse the file
        tag = "w:p" if kind == "docx" else "hp:p"
        # One line per paragraph; a paragraph's runs joined as they print.
        lines = []
        for paragraph in re.findall(rf"<{tag}[ >].*?</{tag}>", xml, re.S):
            runs = re.findall(r"<(?:w|hp):t(?: [^>]*)?>([^<]*)</(?:w|hp):t>", paragraph)
            lines.append("".join(runs))
        text = "\n".join(lines)
    return re.sub(r"[ \t ]+", " ", text)


@pytest.mark.parametrize("kind", ["docx", "pdf", "hwpx"])
@pytest.mark.parametrize("head", list(CASES))
def test_each_head_type_carries_its_fields_and_numbers(head: str, kind: str):
    block = _block(head)
    text = _text(kind, "시험 문서", SECTIONS, block)

    assert "시험 문서" in text
    for label, value in block["fields"]:
        # A paper names its authors and affiliation without the labels.
        if not (head == "paper" and value):
            assert label in text, (label, kind)
        if value:
            assert value in text, (value, kind)
    # A value the request never gave is a blank, printed as one — never invented.
    assert any(not value for _, value in block["fields"])
    assert doc_formats.BLANK in text

    if block["numbering"] == "none":
        assert "1. 서론" not in text and "서론" in text
    else:
        for heading in NUMBERED[block["numbering"]]:
            assert heading in text, (heading, kind)

    if head == "press":
        assert "보도자료" in text and "부제" in text
    if head == "memo":
        assert "제목" in text
    if head == "paper":
        assert "초록" in text and "핵심어" in text and "검색, 요약" in text


@pytest.mark.parametrize("kind", ["docx", "pdf", "hwpx"])
def test_a_document_without_a_title_block_is_unnumbered(kind: str):
    text = _text(kind, "시험 문서", SECTIONS, None)
    for heading in ("서론", "배경", "목적", "결론"):
        assert heading in text
    for numbered in ("1. 서론", "1.1 배경", "I. 서론", "가. 배경"):
        assert numbered not in text
    assert doc_formats.BLANK not in text


def test_no_title_block_leaves_the_files_as_they_were():
    plain = zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", SECTIONS)))
    empty = zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", SECTIONS, title_block={})))
    assert plain.read("word/document.xml") == empty.read("word/document.xml")
    plain = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", SECTIONS)))
    empty = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("제목", SECTIONS, title_block=None)))
    assert plain.read("Contents/section0.xml") == empty.read("Contents/section0.xml")


def test_the_title_block_heads_page_one_without_a_cover_page():
    """No cover page: the title block and the first section share page one."""
    pdf = report_export.to_pdf("시험 문서", SECTIONS, title_block=_block("cover"))
    first = PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or ""
    assert "담당교수" in first and "서론" in first
    docx = (
        zipfile.ZipFile(
            io.BytesIO(report_export.to_docx("시험 문서", SECTIONS, title_block=_block("cover")))
        )
        .read("word/document.xml")
        .decode()
    )
    assert 'w:type="page"' not in docx[: docx.index("서론")]
    hwpx = (
        zipfile.ZipFile(
            io.BytesIO(report_export.to_hwpx("시험 문서", SECTIONS, title_block=_block("cover")))
        )
        .read("Contents/section0.xml")
        .decode()
    )
    assert 'pageBreak="1"' not in hwpx[: hwpx.index("서론")]


def test_the_web_mirror_is_the_same_table():
    source = (
        Path(__file__).resolve().parents[3] / "apps/web/src/components/report/docFormats.ts"
    ).read_text(encoding="utf-8")
    table = re.search(r"/\* wire:begin \*/(.*?)/\* wire:end \*/", source, re.S)
    assert table, "docFormats.ts must keep the table between wire markers"
    assert json.loads(table.group(1)) == doc_formats.wire()
    assert f"BLANK = '{doc_formats.BLANK}'" in source
