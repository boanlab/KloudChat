"""HTML 표면에서 쓴 문서도 같은 블록을 내보내는가.

The document surface writes two ways. One produces Markdown, and its strips,
procedures and charts have been reaching the exporters since they were built.
The other produces HTML through a 서식 — and `page_export` read that markup with
a vocabulary that predates all three.

The failure was silent and total: a strip of figures is a `<div>` holding
`<strong>` and `<span>`, and none of those is a text tag, so the numbers were
collected by nothing. The `.docx` came out with the sentence before the strip,
the sentence after it, and no strip.
"""

from __future__ import annotations

from app.services import page_export, report_export

_PAGE = (
    "<section>"
    "<h2>도입 성과</h2>"
    "<p>세 지표가 함께 움직였다.</p>"
    '<div class="kpi">'
    "<div><strong>32%</strong><span>오탐 감소</span></div>"
    "<div><strong>1.4초</strong><span>평균 응답</span></div>"
    "</div>"
    '<ol class="steps">'
    "<li><strong>자료 수집</strong> <span>공개 데이터를 모은다</span></li>"
    "<li><strong>정제</strong> <span>중복을 걸러낸다</span></li>"
    "</ol>"
    '<figure class="chart" data-source="bar | 건&#10;분기 | 처리&#10;'
    '1분기 | 120&#10;2분기 | 210"></figure>'
    "<table><tr><th>기준</th><th>값</th></tr><tr><td>비용</td><td>3억</td></tr></table>"
    "</section>"
)


def test_the_reader_finds_all_three() -> None:
    block = page_export.read(_PAGE)[0]
    assert block["metrics"] == [["32%", "오탐 감소"], ["1.4초", "평균 응답"]]
    assert block["steps"][0][0] == "자료 수집"
    assert len(block["steps"]) == 2
    assert block["charts"] and "1분기 | 120" in block["charts"][0]


def test_they_come_out_as_the_fences_the_exporters_read() -> None:
    """A document written on the HTML surface exports as one written on the
    Markdown surface. Two paths, one file."""
    content = page_export.to_sections(_PAGE)[0]["content"]
    kinds = [line[0] for line in report_export._markdown_to_lines(content)]
    assert "kpi" in kinds
    assert "steps" in kinds
    assert "chart" in kinds
    assert "table" in kinds


def test_a_table_stays_a_table() -> None:
    """It used to arrive as `- 기준 · 값`.

    The exporters have drawn a real table from a GFM one since they learned
    how, and a row of middots is a comparison the reader has to rebuild.
    """
    content = page_export.to_sections(_PAGE)[0]["content"]
    assert "| 기준 | 값 |" in content
    assert "기준 · 값" not in content


def test_the_figures_actually_reach_a_file() -> None:
    import io
    import zipfile

    sections = page_export.to_sections(_PAGE)
    with zipfile.ZipFile(io.BytesIO(report_export.to_docx("제목", sections))) as archive:
        document = archive.read("word/document.xml").decode()
    for text in ("32%", "오탐 감소", "자료 수집"):
        assert text in document


def test_a_page_with_none_of_them_is_unchanged() -> None:
    plain = "<section><h2>요약</h2><p>한 문단.</p><ul><li>가</li></ul></section>"
    content = page_export.to_sections(plain)[0]["content"]
    assert content == "한 문단.\n\n- 가"


def test_an_edited_section_keeps_every_figure_in_the_strip() -> None:
    """The other reader of the same markup.

    A section touched in the document editor is stored as `format: "html"` and
    reaches the exporters through `richtext.to_markdown`, not through
    `page_export`. That reader matched the strip with a lazy `</div>` — and a
    strip is a `<div>` whose cells are `<div>`s, so it closed on the first
    cell. One figure came out as a fence and every figure after it as loose
    prose: `**68%**기초 도구 사용 한계`, run together without so much as a
    space, which is what the exported .docx and .hwpx printed.
    """
    from app.services import richtext

    strip = (
        '<div class="kpi">'
        "<div><strong>32%</strong><span>고급 AI 활용 가능</span></div>"
        "<div><strong>68%</strong><span>기초 도구 사용 한계</span></div>"
        "<div><strong>4.2</strong><span>월 평균 학습 주기</span></div>"
        "<div><strong>12</strong><span>주 교육 필요 기간</span></div>"
        "</div><p>뒤 문단</p>"
    )
    markdown = richtext.to_markdown(strip)

    assert markdown.startswith("```kpi\n")
    fence = markdown.split("```")[1].removeprefix("kpi\n").strip().splitlines()
    assert fence == [
        "32% | 고급 AI 활용 가능",
        "68% | 기초 도구 사용 한계",
        "4.2 | 월 평균 학습 주기",
        "12 | 주 교육 필요 기간",
    ]
    # Nothing escaped the fence to become prose.
    assert "**68%**" not in markdown
    assert "뒤 문단" in markdown


def test_two_strips_in_one_section_stay_separate() -> None:
    """The balanced walk must stop at *this* strip's close, not run to the
    document's last `</div>` and swallow what is between them."""
    from app.services import richtext

    markdown = richtext.to_markdown(
        '<div class="kpi"><div><strong>1</strong><span>가</span></div></div>'
        "<p>사이 문단</p>"
        '<div class="kpi"><div><strong>2</strong><span>나</span></div>'
        "<div><strong>3</strong><span>다</span></div></div>"
    )

    assert markdown.count("```kpi") == 2
    assert "사이 문단" in markdown
    assert "3 | 다" in markdown
