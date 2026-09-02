"""카드 격자와 강조 상자가, 다니는 모든 문을 지나 살아남는가.

이 두 블록은 다른 블록들과 같은 길을 다닌다. 모델이 펜스로 쓰고, 웹뷰가
그리고, 페이지뷰가 HTML 로 바꿔 저장하고, 세 내보내기가 다시 읽는다. 길이
넷이므로 끊어지는 자리도 넷이다 — 그리고 끊어지면 조용히 끊어진다: 격자가
느슨한 소제목 묶음으로 돌아오고, 다음 저장이 그것을 문서로 만든다.

`kpi` 와 `steps` 가 이미 그 길을 냈고, 이 시험은 새 둘이 같은 길 위에 있는지
만 확인한다.
"""

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
    """두 단이므로 홀수면 짝이 빈다. 마지막 카드가 폭을 다 먹으면 그 카드가
    다른 종류의 것으로 읽히는데, 그것은 사실이 아니다."""
    pairs = report_export._in_pairs([("가", []), ("나", []), ("다", [])])
    assert [len(row) for row in pairs] == [2, 2]
    assert pairs[1][1] == ("", [])


def test_an_edited_section_comes_back_as_the_same_fence():
    """페이지뷰에서 한 글자만 고쳐도 절은 HTML 로 저장된다. 내보내기는
    마크다운을 읽으므로, 돌아오는 길이 없으면 격자는 거기서 끝난다."""
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
    """`_CONSTRUCT` 의 순서가 이 시험의 전부다. 카드 대안이 `<h3>` 나 `<ul>`
    뒤에 놓이면 격자는 소제목 둘과 목록 둘로 흩어져 돌아온다."""
    back = richtext.to_markdown(_MARKUP)
    assert "### 산출물" not in back
    assert back.startswith("```cards")


def test_the_seed_styles_what_the_editor_writes():
    """서식이 그리지 않는 class 는 모델에게 준 적 없는 어휘다."""
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
    """그림이 아니라 표로 나가야 한다.

    격자를 래스터로 내보내면 받은 사람이 산출물 한 줄을 고칠 수도, 이름을
    찾을 수도 없다. 세 형식 모두 만들어지는지, 그리고 글자가 파일 안에 실제로
    들어 있는지까지 본다 — 만들어지기만 하고 비어 있는 파일이 이 블록에서
    가장 있을 법한 실패다.
    """
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
