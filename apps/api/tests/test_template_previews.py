"""Each document 서식's gallery preview is a finished document in that format.

The sample opens with the head block the product draws, carries every section of the
format's outline in order, and its figures hold: rows add up to their total, 부가세 is 10%
of 공급가액, 수량 × 단가 is the 금액, a 「금…원(금…원)」 reads the same in both, and every
date's weekday is the calendar's. The preview route draws it with the web's own format rules
(`format_css`), whose copies are kept equal to the TypeScript here, and a column of words is
never set flush right as if it were figures.
"""

from __future__ import annotations

import datetime as dt
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import workspace as workspace_router
from app.services import design_templates, doc_formats, format_css, table_figures

_WEB = Path(__file__).resolve().parents[3] / "apps/web/src"
_DOCS = sorted(t.id for t in design_templates.all_templates() if t.id.startswith("doc-"))
_FOLDERS = Path(design_templates.__file__).resolve().parents[1] / "design_templates"
_W = "word/document.xml"
_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(workspace_router.router)
    return TestClient(app)


class _Tables(HTMLParser):
    """Body tables as rows of cell text; the head block's field table is left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._skip or "tb-fields" in (dict(attrs).get("class") or ""):
                self._skip += 1
            else:
                self.tables.append([])
        elif self._skip:
            return
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._skip:
            self._skip -= 1
        elif self._skip:
            return
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _tables(html: str) -> list[list[list[str]]]:
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def _number(cell: str) -> float | None:
    found = re.match(r"^[−-]?[\d,]+(?:\.\d+)?", cell.replace("−", "-"))
    if not found or not table_figures.NUMERIC_CELL.match(cell):
        return None
    return float(found.group(0).replace(",", ""))


def _outline(template: design_templates.DesignTemplate) -> list[str]:
    """The format's sections in order: the 개조식 보고's fixed outline, else the form's
    top-level headings (a heading repeated in the form counts once)."""
    if doc_formats.TEMPLATE_FORMATS.get(template.id) == "gaejo":
        return list(doc_formats.GAEJO_OUTLINE)
    document = ET.fromstring(zipfile.ZipFile(template.form_file).read(_W))
    out: list[str] = []
    for para in document.iter(f"{_NS}p"):
        style = para.find(f"{_NS}pPr/{_NS}pStyle")
        if style is not None and style.get(f"{_NS}val") == "Heading1":
            text = "".join(node.text or "" for node in para.iter(f"{_NS}t")).strip()
            if text not in out:
                out.append(text)
    return out


# ── the samples ───────────────────────────────────────────────────────


@pytest.mark.parametrize("template_id", _DOCS)
def test_the_sample_opens_with_the_head_the_product_draws(template_id: str) -> None:
    template = design_templates.get(template_id)
    sample = re.sub(r"<!--.*?-->", "", template.sample, flags=re.S).strip()
    fmt = doc_formats.BY_ID.get(doc_formats.TEMPLATE_FORMATS.get(template_id, ""))
    if fmt is None:
        # No format: the product heads the page with the title alone.
        assert re.match(r'<div class="cover"><h1>[^<]+</h1></div>', sample), template_id
        return
    head = re.match(r'<header class="([^"]+)">(.*?)</header>', sample, re.S)
    assert head, f"{template_id}: the sample must open with the format's head block"
    expected = f"tb tb-{fmt.head}" + (" cover" if fmt.head == "cover" else "")
    expected += " tb-onepage" if format_css.one_page(fmt) else ""
    assert head.group(1) == expected
    assert '<h1 class="tb-title">' in head.group(2)
    text = re.sub(r"<[^>]+>", " ", head.group(2))
    for label in fmt.fields:
        assert label in text, f"{template_id}: head field 「{label}」 missing"


@pytest.mark.parametrize("template_id", _DOCS)
def test_the_sample_has_every_section_of_its_outline_in_order(template_id: str) -> None:
    template = design_templates.get(template_id)
    shown = [h.strip() for h in re.findall(r"<h2[^>]*>(.*?)</h2>", template.sample, re.S)]
    outline = _outline(template)
    body = [name for name in outline if name != "붙임" or "붙임" in shown]
    if "붙임" in outline and "붙임" not in shown:
        # The 개조식 붙임 has no heading of its own: it is the 「붙임  …」 line.
        assert 'class="gaejo-attach">붙임' in template.sample, template_id
    assert shown == body, f"{template_id}: {shown} != {body}"


@pytest.mark.parametrize("template_id", _DOCS)
def test_the_samples_figures_hold(template_id: str) -> None:
    sample = design_templates.get(template_id).sample
    checked = 0
    for rows in _tables(sample):
        head, body = rows[0], rows[1:]
        labels = [row[0] for row in body]
        summary = {"공급가액", "부가세", "합계", "총계", "총액", "계"}
        first = next((i for i, label in enumerate(labels) if label in summary), None)
        if first is None:
            continue
        items = body[:first]
        for column in range(1, len(head)):
            values = {row[0]: _number(row[column]) for row in body[first:] if column < len(row)}
            listed = [_number(row[column]) for row in items if column < len(row)]
            if not any(v is not None for v in values.values()) or None in listed:
                continue
            total = sum(listed)  # type: ignore[arg-type]
            if values.get("공급가액") is not None:
                supply = values["공급가액"]
                assert supply == total, f"{template_id}: 공급가액 {supply} != {total}"
                assert values.get("부가세") == supply // 10, f"{template_id}: 부가세"
                assert values.get("합계") == supply + values["부가세"], f"{template_id}: 합계"
            else:
                closing = next(v for k, v in values.items() if k in summary and v is not None)
                assert abs(closing - total) < 0.05, f"{template_id}: {head[column]} {closing}"
            checked += 1
        # 수량 × 단가 = 금액, and a 「단가 × 수량」 basis equals its 금액.
        names = [re.sub(r"\(.*\)", "", h) for h in head]
        if {"수량", "단가", "금액"} <= set(names):
            q, p, a = names.index("수량"), names.index("단가"), names.index("금액")
            for row in items:
                assert _number(row[q]) * _number(row[p]) == _number(row[a]), row
        if "산출 근거" in names:
            basis, amount = names.index("산출 근거"), len(head) - 1
            for row in items:
                x, y = re.findall(r"[\d,]+", row[basis])[:2]
                product = int(x.replace(",", "")) * int(y.replace(",", ""))
                assert product == _number(row[amount]), row
    if template_id in ("doc-client-proposal", "doc-event-plan", "doc-gaejo-report"):
        assert checked, f"{template_id}: its money table was not found"


@pytest.mark.parametrize("template_id", _DOCS)
def test_the_samples_amounts_and_dates_read_true(template_id: str) -> None:
    sample = design_templates.get(template_id).sample
    for digits, words in re.findall(r"금([\d,]+)원\(금([가-힣]+)원\)", sample):
        assert words == doc_formats.hangul_amount(int(digits.replace(",", ""))), digits
    weekdays = "월화수목금토일"
    year = None
    pattern = r"(?:(\d{4})\.\s)?(\d{1,2})\.\s(\d{1,2})\.\(([월화수목금토일])\)"
    for found in re.finditer(pattern, sample):
        year = int(found.group(1) or year or 0)
        assert year, found.group(0)
        day = dt.date(year, int(found.group(2)), int(found.group(3)))
        assert weekdays[day.weekday()] == found.group(4), f"{template_id}: {found.group(0)}"


@pytest.mark.parametrize("template_id", _DOCS)
def test_a_closing_format_ends_its_sample_with_kkeut(template_id: str) -> None:
    fmt = doc_formats.BY_ID.get(doc_formats.TEMPLATE_FORMATS.get(template_id, ""))
    sample = design_templates.get(template_id).sample
    if fmt is not None and fmt.closing:
        assert re.search(r"  끝\.</p>\s*</section>\s*$", sample), template_id


# ── the preview ───────────────────────────────────────────────────────


def test_the_preview_numbers_marks_and_aligns_as_the_product_does() -> None:
    with _client() as client:
        gaejo = client.get("/design-templates/doc-gaejo-report/preview").text
        comparison = client.get("/design-templates/doc-comparison/preview").text
    # The format's own rules and its element.
    assert '<div class="fmt gaejo tb-scaled f-gaejo">' in gaejo
    assert ".tb.tb-onepage .tb-box" in gaejo
    assert '<h2 data-num="1.">현황</h2>' in gaejo
    # Figures flush right, words left: 비용 is figures, the 기준 column words.
    assert '<td class="fig">21,600,000</td>' in comparison
    assert "<td>연 비용(원)</td>" in comparison
    assert '<span class="doc-placeholder">[담당자]</span>' in comparison


def test_no_document_template_sets_a_column_right_by_its_position() -> None:
    for template_id in _DOCS:
        path = _FOLDERS / template_id / "design.css"
        css = re.sub(
            r"/\*.*?\*/", "", path.read_text(encoding="utf-8") if path.exists() else "", flags=re.S
        )
        for rule in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            if "text-align: right" in rule[1] and re.search(r"t[dh]:(?:not|last|nth)", rule[0]):
                pytest.fail(f"{template_id}: {rule[0].strip()} right-aligns by position")


def test_mark_html_marks_figures_and_totals_only() -> None:
    marked = table_figures.mark_html(
        "<table><thead><tr><th>기준</th><th>A사</th><th>금액</th></tr></thead><tbody>"
        "<tr><td>재택</td><td>주 3일</td><td>1,000원</td></tr>"
        "<tr><td>합계</td><td>가능</td><td>1,000원</td></tr></tbody></table>"
    )
    assert "<th>A사</th>" in marked and "<td>주 3일</td>" in marked
    assert '<th class="fig">금액</th>' in marked and '<td class="fig">1,000원</td>' in marked
    assert '<tr class="total"><td>합계</td>' in marked
    # A head block's field table is labels beside values, never figures.
    fields = (
        '<table class="tb-fields"><tr><th>조</th><td>3</td></tr>'
        "<tr><th>a</th><td>4</td></tr></table>"
    )
    assert table_figures.mark_html(fields) == fields


# ── the web copies ────────────────────────────────────────────────────


def _ts_const(source: str, name: str) -> str:
    found = re.search(rf"(?:export )?const {name} = `(.*?)`\n", source, re.S)
    assert found, name
    return found.group(1)


def test_the_format_rules_are_the_webs_verbatim() -> None:
    formats = (_WEB / "components/report/docFormats.ts").read_text(encoding="utf-8")
    marks = (_WEB / "lib/placeholders.ts").read_text(encoding="utf-8")
    placeholder = re.search(r"export const PLACEHOLDER_CSS = '(.*?)'\n", marks).group(1)
    assert format_css.PLACEHOLDER_CSS == placeholder
    title = _ts_const(formats, "TITLE_BLOCK_CSS").replace("${PLACEHOLDER_CSS}", placeholder)
    assert format_css.TITLE_BLOCK_CSS == title
    assert format_css.GAEJO_CSS == _ts_const(formats, "GAEJO_CSS")
    assert format_css.CORE_CSS == _ts_const(formats, "CORE_CSS")
    # numberingCss: the same rules, the content-number expression filled in.
    for numbering, expression in (("decimal", 'counter(tba) "." counter(tbb)'),):
        for line in format_css.numbering_css(numbering).splitlines():
            assert line.replace(expression, "${CONTENT_NUMBER[numbering]}") in formats, line
    assert "(?:callout|cards)" in formats and "(?:callout|cards)" in format_css._CALLOUT.pattern


def test_the_format_scale_is_the_webs() -> None:
    scale = (_WEB / "components/report/docType.ts").read_text(encoding="utf-8")
    css = format_css.format_scale_css("gaejo")
    for rule in (
        ".fmt.f-gaejo section { margin: 0 !important; }",
        ".fmt.f-gaejo h2 { margin: 7pt 0 2pt !important; font-size: var(--doc-h1) !important; }",
        "@page { margin: 20mm 20mm 15mm 20mm; }",
    ):
        assert rule in css
    for template in (
        "${at} section { margin: 0 !important; }",
        "${at} h2 { margin: ${space.headingBefore}pt 0 ${space.headingAfter}pt !important;",
    ):
        assert template in scale


def test_the_figure_rule_is_the_webs() -> None:
    source = (_WEB / "components/report/figureColumns.ts").read_text(encoding="utf-8")
    for name in ("NUMERIC_HEAD", "NUMERIC_CELL", "DATE_CELL", "TOTAL_LABEL"):
        found = re.search(rf"export const {name} = /(.*)/\n", source)
        assert found, name
        assert found.group(1).replace("\\/", "/") == getattr(table_figures, name).pattern, name
    words = re.search(r"export const NO_FIGURE = new Set\(\[(.*)\]\)", source).group(1)
    assert set(re.findall(r"'([^']*)'", words)) == table_figures.NO_FIGURE
