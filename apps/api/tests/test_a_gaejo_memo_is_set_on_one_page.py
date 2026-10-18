"""One-page 공문서 setting of a 개조식 보고 in every renderer."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from app.services import doc_formats, doc_type, fonts, report_export

_WEB = Path(__file__).resolve().parents[3] / "apps/web/src/components/report"
_SEED = Path(__file__).resolve().parents[3] / "apps/api/app/design_templates/_document/seed.html"

#: A 개조식 보고's sections, as a model wrote them.
SECTIONS = [
    {"id": "s0", "heading": "현황", "level": 1, "content": (
        "```callout\n핵심\n3호기 조건부 합격 및 2026년 9월 기준 고장 신고 19건 발생 확인\n"
        "권상기 마모 보수 및 고장 신고 처리\n```\n\n"
        "- 본관 엘리베이터 3대 현황\n  - 설치 연도: 1998년(사용 경과 28년)\n"
        "  - 최근 정밀안전검사 결과: 3호기 '조건부 합격' (권상기 마모, 1년 내 보수 조건)\n"
        "- 고장 및 사고 발생 현황\n  - 2025년 고장 신고: 14건\n  - 2026년(1~9월) 고장 신고: 19건\n"
        "  - 2026년 8월 2호기 갇힘 사고 발생: 1건 (119 출동, 부상 없음)")},
    {"id": "s1", "heading": "문제점 및 검토", "level": 1, "content": (
        "- 법적 안전기준 및 수명 문제\n"
        "  - 승강기 안전관리법상 설치 후 25년 경과 시 3년 주기 정밀안전검사 의무 부과\n"
        "  - 설비 노후화로 고장 빈도 증가 및 안전사고 위험 상존\n"
        "- 교체 사업 규모 및 기간\n  - 1대 교체 비용: 1억 2천만 원 (총 3대 기준 3억 6천만 원)\n"
        "  - 1대 교체 소요 기간: 6주\n  - 3대 전체 순차 교체 예상 소요 기간: 18주\n"
        "- 학사 일정 및 예산 제약 사항\n"
        "  - 3대 동시 교체 시 본관 6개 층 계단 이용 불가로 학사 일정 차질 예상\n"
        "  - 내년도 시설 개보수 예산(총 9억 원)에서 기숙사 방수(2억 5천만 원) 등 우선 사업과 겹침\n"
        "  - 1년 내 3대 전체 교체 예산 확보 곤란 (예산팀 의견)")},
    {"id": "s2", "heading": "향후 조치 및 결정 요청", "level": 1, "content": (
        "- 순차 교체 일정안 (담당 의견)\n"
        "  - 1차: 3호기 교체 (2026년 동계 방학, 12월 22일~2027. 2. 27. 내)\n"
        "  - 2차: 나머지 2대 교체 (2027년 하계 및 동계 방학 분산)\n"
        "  - ※ 방학 기간 공사에 집중하여 학기 중 운행 차질 최소화\n"
        "- 결재 요청 사항\n  - 1호안: 3호기 우선 교체 사업 승인\n"
        "  - 2호안: 2027년 예산 요구서에 2대분 금액(2억 4천만 원) 반영 승인")},
    {"id": "s3", "heading": "붙임", "level": 1,
     "content": "1. 본관 엘리베이터 교체 견적서 및 안전검사 결과서 1부."},
]
BLOCK = {
    "format": "gaejo", "label": "개조식 보고서", "head": "memo", "numbering": "korean",
    "lists": "gaejo", "closing": True,
    "fields": [["보고일", ""], ["보고자(부서)", "시설팀 김도윤 주무관"], ["보고 대상", "시설과장"]],
}
TITLE = "본관 엘리베이터 교체 계획 및 예산 승인 요청"
SERIF = {"font": "serif", "accent": "#334155", "ink": "#111827", "muted": "#6b7280"}
GAEJO = doc_type.type_for("gaejo")
SPACE = doc_type.space_for("gaejo")
#: Fonts decide line breaks; the page counts hold where the Nanum faces are installed.
needs_faces = pytest.mark.skipif(
    not (fonts.embedded("serif") and fonts.embedded("gothic")), reason="no Nanum faces"
)


# ── the body ───────────────────────────────────────────────────────────


def test_a_note_under_an_item_stands_at_that_item_s_depth():
    assert doc_formats.gaejo_markdown("- 일정\n  - 1차 교체\n※ 방학 중 공사") == (
        "- 일정\n  - 1차 교체\n  - ※ 방학 중 공사"
    )
    # Not after an item: a note of its own.
    assert doc_formats.gaejo_markdown("※ 9월 기준\n- 현황") == "※ 9월 기준\n\n- 현황"
    block = report_export._title_block(BLOCK)
    lines = report_export._section_lines(SECTIONS[2], block, False, "  ")
    note = next(line for line in lines if "방학 기간" in str(line[1]))
    assert note[0] == "remark" and note[2] == "※" and note[3] == 1
    assert not any(str(text).startswith("※") for kind, text, *_ in lines if kind == "gaejo")


def test_the_attachment_is_the_closing_lines_not_a_section():
    block = report_export._title_block(BLOCK)
    lines = report_export._section_lines(SECTIONS[3], block, True, "  ")
    item = "1. 본관 엘리베이터 교체 견적서 및 안전검사 결과서 1부.  끝."
    assert lines == [("attach", item, "붙임", 0)]
    numbers = report_export._HeadingNumbers(block)
    assert [numbers.section(s) for s in SECTIONS] == ["1. ", "2. ", "3. ", ""]
    # Typed as a list item, or with nothing attached.
    typed = {"heading": "붙임", "content": "- 붙임\n1. 견적서 1부.\n2. 결과서 1부."}
    assert [line[0] for line in report_export._section_lines(typed, block, True, "  ")] == [
        "attach", "attach"
    ]
    assert report_export._drawn_sections([*SECTIONS[:1], {"heading": "붙임", "content": "없음"}],
                                         block) == SECTIONS[:1]


def test_a_table_moved_to_the_attachment_follows_the_closing_on_its_own_page():
    block = report_export._title_block(BLOCK)
    moved = {"heading": "붙임", "content": (
        "1. 견적서 1부.\n\n붙임 1.\n표: 대안 비교\n| 안 | 비용 |\n|---|---|\n| 순차 | 3억 |")}
    lines = report_export._section_lines(moved, block, True, "  ")
    kinds = [(kind, str(text)) for kind, text, *_ in lines]
    assert kinds[0] == ("attach", "1. 견적서 1부.  끝.")
    assert kinds[1][0] == "pagebreak" and ("table" in [k for k, _ in kinds[2:]])


# ── PDF ────────────────────────────────────────────────────────────────


def _pdf_runs(data: bytes) -> list[tuple[str, float, float]]:
    """`(text, size, y)` of every text run on page one."""
    from pypdf import PdfReader

    runs: list[tuple[str, float, float]] = []

    def visit(text, cm, tm, font, size):
        if text.strip():
            runs.append((text.strip(), size * tm[3], cm[5] + tm[5] * cm[3]))

    PdfReader(io.BytesIO(data)).pages[0].extract_text(visitor_text=visit)
    return runs


def test_the_pdf_closes_with_the_attachment_lines_and_a_bare_note_marker():
    data = report_export.to_pdf(TITLE, SECTIONS, tokens=SERIF, title_block=BLOCK)
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert "4. 붙임" not in text and "붙임" in text and "1부.  끝." in text.replace(" ", " ")
    assert not re.search(r"○\s*※", text)


@needs_faces
def test_the_pdf_sets_the_memo_scale_and_fits_one_page():
    data = report_export.to_pdf(TITLE, SECTIONS, tokens=SERIF, title_block=BLOCK)
    assert report_export.pdf_pages(data) == (1, 1)
    runs = _pdf_runs(data)
    body = [r for r in runs if r[0].startswith("2025년 고장") or r[0].startswith("2026년(1~9월)")]
    assert len(body) == 2 and all(abs(size - GAEJO["body"]) < 0.01 for _, size, _ in body)
    # Items close under each other: one 160% line apart, no paragraph space.
    assert abs((body[0][2] - body[1][2]) - GAEJO["body"] * 1.6) < 0.2
    head = next(r for r in runs if "현황" in r[0] and r[1] > GAEJO["body"])
    assert abs(head[1] - GAEJO["h1"]) < 0.01


@needs_faces
def test_the_hwpx_estimate_fits_one_page():
    assert report_export.hwpx_pages(TITLE, SECTIONS, tokens=SERIF, title_block=BLOCK) == (1, 1)


# ── DOCX ───────────────────────────────────────────────────────────────


def _docx():
    from docx import Document

    from app.services import design_templates

    template = design_templates.get("doc-gaejo-report")
    data = report_export.to_docx(
        TITLE, SECTIONS, tokens=SERIF, template=template.docx_template if template else "",
        title_block=BLOCK, body_from_design=True,
    )
    return Document(io.BytesIO(data))


def test_the_docx_closes_with_the_attachment_lines_and_a_bare_note_marker():
    document = _docx()
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert headings == ["1. 현황", "2. 문제점 및 검토", "3. 향후 조치 및 결정 요청"]
    closing = [p.text for p in document.paragraphs if p.text.strip()][-1]
    assert closing.startswith("붙임\t1. 본관") and closing.endswith("1부.  끝.")
    note = next(p for p in document.paragraphs if "방학 기간" in p.text)
    assert note.text.startswith("※\t")


def test_the_docx_carries_the_memo_scale_spacing_and_page():
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    document = _docx()
    section = document.sections[0]
    margins = [section.top_margin, section.bottom_margin, section.left_margin, section.right_margin]
    assert [round(m.mm, 1) for m in margins] == [20, 15, 20, 20]
    body = document.styles["Body Text"].paragraph_format
    assert document.styles["Body Text"].font.size == Pt(GAEJO["body"])
    assert body.line_spacing_rule == WD_LINE_SPACING.AT_LEAST
    assert body.line_spacing == Pt(GAEJO["body"] * 1.6)
    heading = document.styles["Heading 1"]
    assert heading.font.size == Pt(GAEJO["h1"])
    assert heading.paragraph_format.space_before == Pt(SPACE["headingBefore"])
    assert heading.paragraph_format.space_after == Pt(SPACE["headingAfter"])
    item = next(p for p in document.paragraphs if p.text.startswith("○\t설치 연도"))
    assert item.paragraph_format.space_after == Pt(SPACE["item"])
    # The 핵심 box: the label beside its two lines.
    core = next(t for t in document.tables if t.cell(0, 0).text == "핵심")
    assert len(core.columns) == 2 and len(core.cell(0, 1).paragraphs) == 2


# ── HWPX ───────────────────────────────────────────────────────────────


def _hwpx() -> tuple[str, str]:
    data = report_export.to_hwpx(TITLE, SECTIONS, tokens=SERIF, title_block=BLOCK)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        section = archive.read("Contents/section0.xml").decode()
        return section, archive.read("Contents/header.xml").decode()


def test_the_hwpx_closes_with_the_attachment_lines_and_a_bare_note_marker():
    section, _ = _hwpx()
    text = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", section))
    assert "4. 붙임" not in text and "붙임  1. 본관 엘리베이터" in text and "1부.  끝." in text
    assert "※ 방학 기간" in text and not re.search(r"○\s*※", text)


def test_the_hwpx_carries_the_memo_scale_spacing_and_page():
    section, header = _hwpx()
    heights = dict(re.findall(r'<hh:charPr id="(\d+)" height="(\d+)"', header))
    assert heights["0"] == str(round(GAEJO["body"] * 100))
    assert heights["2"] == str(round(GAEJO["title"] * 100))
    assert heights["3"] == str(round(GAEJO["h1"] * 100))
    shape = re.search(r'<hh:paraPr id="1" .*?</hh:paraPr>', header, re.S).group(0)
    assert f'<hc:prev value="{round(SPACE["headingBefore"] * 100)}" unit="HWPUNIT"/>' in shape
    assert '<hh:lineSpacing type="PERCENT" value="160"' in shape
    mm = 7200 / 25.4
    margin = re.search(r"<hp:margin ([^>]*)/>", section).group(1)
    sides = {k: int(v) for k, v in re.findall(r'(\w+)="(\d+)"', margin)}
    assert sides["left"] == sides["right"] == round(20 * mm)
    assert sides["top"] + sides["header"] == round(20 * mm)
    assert sides["bottom"] + sides["footer"] == round(15 * mm)


# ── page view and print ────────────────────────────────────────────────


def _web_tables(name: str) -> dict[str, dict[str, float]]:
    text = (_WEB / "docType.ts").read_text(encoding="utf-8")
    start = text.index(f"export const {name}:")
    block = text[start : text.index("\n}\n", start)]
    return {
        key: {m.group(1): float(m.group(2)) for m in re.finditer(r"(\w+): ([\d.]+),", body)}
        for key, body in re.findall(r"^  (\w+): \{([^}]*)\}", block, re.M)
    }


def test_the_web_view_reads_the_same_format_scale():
    assert _web_tables("FORMAT_TYPE") == doc_type.FORMAT_TYPE
    assert _web_tables("FORMAT_LEADING") == doc_type.FORMAT_LEADING
    assert _web_tables("FORMAT_SPACE") == doc_type.FORMAT_SPACE
    assert _web_tables("FORMAT_MARGINS") == doc_type.FORMAT_MARGINS


def test_the_page_seed_draws_a_note_item_with_its_own_marker():
    seed = _SEED.read_text(encoding="utf-8")
    assert ".gaejo ul > li.gaejo-note::before { content: none; }" in seed
    formats = (_WEB / "docFormats.ts").read_text(encoding="utf-8")
    assert "li:is(.gaejo-note, [data-note])::before { content: none; }" in formats
    editor = (_WEB / "DocumentEditor.tsx").read_text(encoding="utf-8")
    assert "attachment(section) ? ''" in editor, "the paged preview draws the 붙임 heading"
