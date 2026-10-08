"""Tables, styles and faces in the .docx, .hwpx and .pdf exports: no table wider than the
text column, the head row repeated on every page, figures flush right, Word's own styles
for headings, lists and table text, and faces each program carries."""

from __future__ import annotations

import io
import pathlib
import re
import zipfile

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.services import design_templates
from app.services import report_export as rx

QUOTE = """| 항목 | 수량 | 단위 | 단가 | 금액 | 비고 |
|---|---|---|---|---|---|
| 사업 관리 | 6 | MM | 3,500,000원 | 21,000,000원 | PM 1명 |
| 챗봇 개발 | 8 | MM | 2,700,000원 | 21,600,000원 | 2명 × 4개월 |
| 합계 | | | | 51,810,000원 | |"""

WIDE = (
    "| "
    + " | ".join(f"열{i}" for i in range(8))
    + " |\n|"
    + "---|" * 8
    + "\n"
    + ("| " + " | ".join(["정보통신기반보호시설지정해제심의위원회운영세부지침개정안"] * 8) + " |")
)

SECTIONS = [
    {"heading": "견적", "content": QUOTE},
    {"heading": "넓은 표", "content": WIDE},
    {"heading": "목록", "content": "- 하나\n  - 둘\n    - 셋\n\n### 소제목\n\n본문 한 줄."},
]


def _docx(template: str = "", **kwargs) -> tuple[bytes, str]:
    data = rx.to_docx("표 시험", SECTIONS, template=template, **kwargs)
    return data, zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()


def _hwpx(**kwargs) -> tuple[str, str]:
    archive = zipfile.ZipFile(io.BytesIO(rx.to_hwpx("표 시험", SECTIONS, **kwargs)))
    return (
        archive.read("Contents/header.xml").decode(),
        archive.read("Contents/section0.xml").decode(),
    )


# ── which columns are figures ─────────────────────────────────────────


def test_money_and_count_columns_are_figures_and_labels_are_not():
    grid = [line.strip("|").split("|") for line in QUOTE.splitlines() if "---" not in line]
    grid = [[cell.strip() for cell in row] for row in grid]
    assert rx._numeric_columns(grid) == {1, 3, 4}


def test_a_column_of_figures_is_found_without_a_money_header():
    rows = [
        ["기준", "팀링크", "워크보드", "모아챗"],
        ["연 비용", "탈락(SSO 미포함)", "17,280,000[2]", "19,440,000[3]"],
        ["가중 합계", "탈락", "90.25", "조건 미확인"],
    ]
    # 모아챗: one figure and a placeholder (조건 미확인) is still a column of figures.
    assert rx._numeric_columns(rows) == {2, 3}


def test_dates_and_year_months_stay_left():
    rows = [["이름", "기관", "취득 연월"], ["정보처리기사", "한국산업인력공단", "2018.11"]]
    assert rx._numeric_columns(rows) == set()


# ── .docx ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("template", ["", "doc-minutes", "doc-client-proposal"])
def test_docx_tables_fit_the_text_column_and_repeat_their_head(template: str):
    path = design_templates.get(template).docx_template if template else ""
    data, xml = _docx(path)
    document = Document(io.BytesIO(data))
    section = document.sections[-1]
    text_dxa = int((section.page_width - section.left_margin - section.right_margin) / 635)
    tables = re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.S)
    assert len(tables) == 2
    for table in tables:
        grid = sum(int(w) for w in re.findall(r'<w:gridCol w:w="(\d+)"', table))
        tblw = re.search(r"<w:tblW [^>]*>", table).group(0)
        assert 'w:type="dxa"' in tblw
        width = int(re.search(r'w:w="(\d+)"', tblw).group(1))
        assert grid <= text_dxa + 2 and width <= text_dxa + 2
        assert '<w:tblLayout w:type="fixed"/>' in table
        first_row = re.search(r"<w:tr[ >].*?</w:tr>", table, re.S).group(0)
        assert '<w:tblHeader w:val="true"/>' in first_row


def test_docx_table_text_is_styled_and_figures_sit_right():
    data, _ = _docx(design_templates.get("doc-client-proposal").docx_template)
    document = Document(io.BytesIO(data))
    quote = document.tables[0]
    assert {p.style.name for p in quote.rows[0].cells[0].paragraphs} == {"표 머리"}
    amount = quote.rows[1].cells[4].paragraphs[0]
    assert amount.style.name == "표 숫자"
    assert amount.style.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert quote.rows[1].cells[0].paragraphs[0].style.name == "표 본문"
    assert quote.style.name == "보고서 표"
    # No run-level bold or size: the styles carry the look.
    for row in quote.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                assert all(run.bold is None and run.font.size is None for run in paragraph.runs)


def test_a_plain_docx_gets_the_table_styles_too():
    document = Document(io.BytesIO(_docx()[0]))
    names = {style.name for style in document.styles}
    assert {"표 머리", "표 본문", "표 숫자"} <= names
    assert document.tables[0].rows[1].cells[3].paragraphs[0].style.name == "표 숫자"


def test_docx_nested_bullets_use_word_list_styles():
    document = Document(io.BytesIO(_docx(design_templates.get("doc-minutes").docx_template)[0]))
    styles = [p.style.name for p in document.paragraphs if p.text in ("하나", "둘", "셋")]
    assert styles == ["List Bullet", "List Bullet 2", "List Bullet 3"]


@pytest.mark.parametrize(
    "row", [r for r in design_templates.all_templates() if r.kind == "document"], ids=lambda r: r.id
)
def test_every_word_template_names_word_faces_and_no_theme_blue(row):
    styles = zipfile.ZipFile(row.docx_template).read("word/styles.xml").decode()
    assert "함초롬" not in styles
    for style_id in ("Title", "Heading1", "Heading2", "Heading3", "Heading4"):
        body = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>', styles, re.S)
        assert body, f"{row.id}: {style_id}"
        text = body.group(0)
        # A theme face wins over the named one in Word; a theme colour is Word's blue.
        assert "asciiTheme" not in text and "eastAsiaTheme" not in text, f"{row.id}: {style_id}"
        assert "themeColor" not in text, f"{row.id}: {style_id}"
        assert re.search(r'w:eastAsia="(맑은 고딕|바탕)"', text), f"{row.id}: {style_id}"
    for name in ("ReportTable", "ListBullet2", "ListBullet3"):
        assert f'w:styleId="{name}"' in styles, f"{row.id}: {name}"
    assert "표 숫자" in styles


def test_the_plain_docx_headings_name_their_faces():
    styles = zipfile.ZipFile(io.BytesIO(_docx()[0])).read("word/styles.xml").decode()
    for style_id in ("Heading1", "Heading2", "Heading3"):
        text = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>', styles, re.S)
        assert "asciiTheme" not in text.group(0) and "themeColor" not in text.group(0)


# ── .hwpx ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("left_right", [None, 35, 15])
def test_hwpx_tables_fit_the_text_column_the_page_settings_leave(left_right):
    settings = {"margins": {"left": left_right, "right": left_right}} if left_right else None
    _, body = _hwpx(page_settings=settings)
    column = rx._hwpx_text_width(settings)
    sizes = [int(w) for w in re.findall(r'<hp:sz width="(\d+)" widthRelTo="ABSOLUTE"', body)]
    assert sizes and all(size <= column for size in sizes)
    for table in re.findall(r"<hp:tbl .*?</hp:tbl>", body, re.S):
        first_row = re.search(r"<hp:tr>.*?</hp:tr>", table, re.S).group(0)
        cells = sum(int(w) for w in re.findall(r'<hp:cellSz width="(\d+)"', first_row))
        assert cells <= column


def test_hwpx_head_row_repeats():
    _, body = _hwpx()
    for table in re.findall(r"<hp:tbl .*?</hp:tbl>", body, re.S):
        assert 'repeatHeader="1"' in table
        rows = re.findall(r"<hp:tr>.*?</hp:tr>", table, re.S)
        assert all('header="1"' in cell for cell in re.findall(r"<hp:tc [^>]*>", rows[0]))
        assert all('header="0"' in cell for cell in re.findall(r"<hp:tc [^>]*>", rows[1]))


def test_hwpx_figures_sit_right():
    header, body = _hwpx()
    right = {
        pid
        for pid, align in re.findall(
            r'<hh:paraPr id="(\d+)"[^>]*>\s*<hh:align horizontal="(\w+)"', header
        )
        if align == "RIGHT"
    }
    quote = re.search(r"<hp:tbl .*?</hp:tbl>", body, re.S).group(0)
    amount = re.search(r'<hp:p paraPrIDRef="(\d+)"[^>]*><hp:run[^>]*><hp:t>21,000,000원', quote)
    label = re.search(r'<hp:p paraPrIDRef="(\d+)"[^>]*><hp:run[^>]*><hp:t>사업 관리', quote)
    assert amount.group(1) in right
    assert label.group(1) not in right


def test_hwpx_names_hancom_faces_for_every_language():
    header, _ = _hwpx(tokens={"font": "gothic"})
    faces = re.findall(r'<hh:fontface lang="(\w+)" fontCnt="2">', header)
    assert faces == ["HANGUL", "LATIN", "HANJA", "JAPANESE", "OTHER", "SYMBOL", "USER"]
    assert header.count('face="함초롬바탕"') == 7 and header.count('face="함초롬돋움"') == 7
    # A gothic design sets the body in 돋움 (id 1); headings are 돋움 either way.
    body_shape = re.search(r'<hh:charPr id="0".*?<hh:fontRef hangul="(\d)"', header, re.S)
    assert body_shape.group(1) == "1"
    serif, _ = _hwpx(tokens={"font": "serif"})
    assert re.search(r'<hh:charPr id="0".*?<hh:fontRef hangul="(\d)"', serif, re.S).group(1) == "0"
    assert re.search(r'<hh:charPr id="3".*?<hh:fontRef hangul="(\d)"', serif, re.S).group(1) == "1"


def test_hwpx_paragraph_margins_are_written_as_hancom_writes_them():
    """True lengths under the HwpUnitChar case, doubled in the default (Hancom's storage)."""
    header, _ = _hwpx()
    for shape in re.findall(r"<hh:paraPr .*?</hh:paraPr>", header, re.S):
        case = re.search(r"<hp:case [^>]*HwpUnitChar\">(.*?)</hp:case>", shape, re.S).group(1)
        default = re.search(r"<hp:default>(.*?)</hp:default>", shape, re.S).group(1)
        for name in ("intent", "left", "prev", "next"):
            true = int(re.search(rf'<hc:{name} value="(-?\d+)"', case).group(1))
            stored = int(re.search(rf'<hc:{name} value="(-?\d+)"', default).group(1))
            assert stored == 2 * true
    bullet = re.search(r'<hh:paraPr id="4".*?</hh:paraPr>', header, re.S).group(0)
    assert '<hc:left value="1000" unit="HWPUNIT"/>' in bullet  # 10 pt, as `_HWPX_PARA_SHAPES`


def test_hwpx_body_starts_where_the_page_settings_say():
    _, body = _hwpx(page_settings={"margins": {"top": 20, "bottom": 15}})
    margin = dict(re.findall(r'(\w+)="(\d+)"', re.search(r"<hp:margin [^>]*>", body).group(0)))
    assert int(margin["header"]) + int(margin["top"]) == round(20 * 7200 / 25.4)
    assert int(margin["footer"]) + int(margin["bottom"]) == round(15 * 7200 / 25.4)
    assert int(margin["header"]) > 0 and int(margin["footer"]) > 0


# ── .pdf ───────────────────────────────────────────────────────────────


def test_pdf_tables_fit_the_frame_repeat_their_head_and_set_figures_right(monkeypatch):
    drawn = []
    real = rx._pdf_table

    def keep(*args, **kwargs):
        table = real(*args, **kwargs)
        drawn.append((table, args[3] if len(args) > 3 else kwargs.get("text_width")))
        return table

    monkeypatch.setattr(rx, "_pdf_table", keep)
    rx.to_pdf("표 시험", SECTIONS, page_settings={"margins": {"left": 30, "right": 30}})
    assert len(drawn) == 2
    for table, width in drawn:
        assert table.repeatRows == 1
        assert sum(table._colWidths) <= width + 0.01
    quote = drawn[0][0]
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT

    def cell(r: int, c: int):
        value = quote._cellvalues[r][c]
        return value[0] if isinstance(value, (list, tuple)) else value

    assert cell(1, 4).style.alignment == TA_RIGHT
    assert cell(1, 0).style.alignment == TA_LEFT


def test_a_serif_pdf_draws_hanja_in_the_gothic_face():
    from app.services import fonts

    serif = fonts.korean("serif")
    if not fonts.embedded("serif") or serif == fonts.korean("gothic"):
        pytest.skip("Nanum faces not installed")
    markup = rx._with_fallback("<b>漢字</b> 병기", serif)
    assert f'<font name="{fonts.korean("gothic")}">漢字</font>' in markup
    assert "병기" in markup.split("</font>")[-1]
    # Markup is left alone, and a face that has the glyphs is not touched.
    assert rx._with_fallback("漢字", fonts.korean("gothic")) == "漢字"


# ── page estimate ──────────────────────────────────────────────────────


def test_the_hwpx_page_estimate_counts_the_body_to_the_attachment_line():
    long_body = "\n\n".join(["가나다라마바사 " * 40] * 12)
    sections = [
        {"heading": "현황", "content": long_body},
        {"heading": "붙임", "content": "붙임  1. 장비 현황표 1부.\n\n" + long_body},
    ]
    total, body = rx.hwpx_pages("쪽 수", sections)
    assert 1 <= body < total
    pdf_total, pdf_body = rx.pdf_pages(rx.to_pdf("쪽 수", sections))
    assert pdf_body <= pdf_total
    assert rx.hwpx_pages("한 쪽", [{"heading": "현황", "content": "짧다."}]) == (1, 1)


# ── HTML print faces ───────────────────────────────────────────────────


def test_every_serif_stack_names_the_linux_face_too():
    """fontconfig calls it `NanumMyeongjo`; `'Nanum Myeongjo'` alone fell back in print."""
    root = pathlib.Path(rx.__file__).resolve().parent.parent / "design_templates"
    for css in root.glob("*/design.css"):
        text = css.read_text()
        if "'Nanum Myeongjo'" in text:
            assert "'NanumMyeongjo'" in text, css.parent.name
    html = design_templates.render(
        design_templates.get("doc-report"), title="t", tokens={"font": "serif"}, body=""
    )
    assert "'NanumMyeongjo'" in html


def test_a_tall_hwpx_table_is_anchored_so_it_can_split_and_rows_fit_their_text():
    """A table set as a character cannot split across pages; a tall one is anchored."""
    rows = [["요구 ID", "요구 내용"]] + [
        [f"REQ-{i:02d}", "민원 유형별 응답 시나리오와 상담원 인계 기능 " * 3] for i in range(40)
    ]
    tall = rx._hwpx_table(rows)
    assert 'treatAsChar="0"' in tall and 'pageBreak="CELL"' in tall
    heights = [int(h) for h in re.findall(r'<hp:cellSz width="\d+" height="(\d+)"', tall)]
    assert max(heights) > rx._HWPX_ROW_HEIGHT  # three sentences need more than one line
    short = rx._hwpx_table([["가", "나"], ["다", "라"]])
    assert 'treatAsChar="1"' in short
    assert set(re.findall(r'<hp:cellSz width="\d+" height="(\d+)"', short)) == {
        str(rx._HWPX_ROW_HEIGHT)
    }


def test_hwpx_headings_keep_with_the_next_paragraph():
    header, _ = _hwpx()
    for pid in ("1", "2"):
        shape = re.search(rf'<hh:paraPr id="{pid}".*?</hh:paraPr>', header, re.S).group(0)
        assert 'keepWithNext="1"' in shape
    body = re.search(r'<hh:paraPr id="3".*?</hh:paraPr>', header, re.S).group(0)
    assert 'keepWithNext="0"' in body
