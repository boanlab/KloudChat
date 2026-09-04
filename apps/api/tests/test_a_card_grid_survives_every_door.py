"""카드 격자와 강조 상자가 펜스·웹뷰·페이지뷰·세 내보내기를 모두 지나 살아남는다."""

from __future__ import annotations

from app.services import report_export, richtext

_FENCE = """```cards
## 산출물
- 네트워크 전면 교체
- 클라우드 이전
## 목표
- 8개월 안에 완료
```"""

_MARKUP = (
    '<section class="cards">'
    "<div><h3>산출물</h3><ul><li>네트워크 전면 교체</li><li>클라우드 이전</li></ul></div>"
    "<div><h3>목표</h3><ul><li>8개월 안에 완료</li></ul></div>"
    "</section>"
)


def test_the_writer_s_fence_reaches_the_exporters_as_a_grid():
    cards = report_export._cards(
        "## 산출물\n- 네트워크 전면 교체\n- 클라우드 이전\n## 목표\n- 8개월 안에 완료"
    )
    assert cards == [
        ("산출물", ["네트워크 전면 교체", "클라우드 이전"]),
        ("목표", ["8개월 안에 완료"]),
    ]


def test_a_grid_is_two_at_a_time_and_the_odd_one_gets_an_empty_partner():
    """격자는 두 단이며 홀수 개면 마지막 짝이 빈다."""
    pairs = report_export._in_pairs([("가", []), ("나", []), ("다", [])])
    assert [len(row) for row in pairs] == [2, 2]
    assert pairs[1][1] == ("", [])


def test_an_edited_section_comes_back_as_the_same_fence():
    """HTML 로 저장된 절이 같은 펜스로 돌아온다."""
    assert richtext.to_markdown(_MARKUP).strip() == _FENCE


def test_a_callout_keeps_its_title_and_its_line():
    markup = (
        '<section class="callout"><h3>승인 없이는 시작하지 않는다</h3>'
        "<p>9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다.</p></section>"
    )
    assert richtext.to_markdown(markup).strip() == (
        "```callout\n승인 없이는 시작하지 않는다\n"
        "9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다.\n```"
    )
    assert report_export._callout(
        "승인 없이는 시작하지 않는다\n9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다."
    ) == (
        "승인 없이는 시작하지 않는다",
        ["9월 교무회의 승인 전까지는 계약도 발주도 하지 않는다."],
    )


def test_a_grid_is_not_read_back_as_loose_headings():
    """`_CONSTRUCT` 에서 카드 대안이 `<h3>`/`<ul>` 보다 먼저 온다."""
    back = richtext.to_markdown(_MARKUP)
    assert "### 산출물" not in back
    assert back.startswith("```cards")


def test_the_seed_styles_what_the_editor_writes():
    """편집기가 쓰는 class 를 서식이 모두 그린다."""
    from app.services import design_templates as dt

    seed = dt.get("doc-report").seed
    assert ".cards" in seed and ".callout" in seed
    assert "section" in dt._ALLOWED_TAGS


_SECTIONS = [
    {
        "heading": "개요",
        "content": (
            "본문 한 문단.\n\n"
            "```cards\n"
            "## 산출물\n- 네트워크 전면 교체\n- 클라우드 이전\n"
            "## 목표\n- 8개월 안에 완료\n"
            "## 이해관계자\n- 정보전략팀\n"
            "```\n\n"
            "```callout\n승인 없이는 시작하지 않는다\n9월 회의 전까지 발주하지 않는다.\n```\n"
        ),
    }
]


def test_all_three_files_carry_the_grid_as_words():
    """세 내보내기 모두 격자를 그림이 아닌 글자로 담는다."""
    import io
    import zipfile

    docx = zipfile.ZipFile(io.BytesIO(report_export.to_docx("검토", _SECTIONS)))
    document = docx.read("word/document.xml").decode()
    for word in ("산출물", "클라우드 이전", "이해관계자", "승인 없이는 시작하지 않는다"):
        assert word in document, f".docx 에 '{word}' 가 없다"

    pdf = report_export.to_pdf("검토", _SECTIONS)
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000

    hwpx = zipfile.ZipFile(io.BytesIO(report_export.to_hwpx("검토", _SECTIONS)))
    section = next(n for n in hwpx.namelist() if n.endswith("section0.xml"))
    text = hwpx.read(section).decode()
    for word in ("산출물", "이해관계자"):
        assert word in text, f".hwpx 에 '{word}' 가 없다"
