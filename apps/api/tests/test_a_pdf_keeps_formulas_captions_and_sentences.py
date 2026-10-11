"""A research plan PDF draws 「Σᵢ wᵢ」, keeps 「표 1.」 on the page of its table, and does not
break a paragraph after 「vs.」."""

from __future__ import annotations

import io

from pypdf import PdfReader

from app.services import report, report_export


def test_a_subscript_letter_the_face_lacks_is_set_as_a_subscript():
    assert report_export._script_markup("ᵢ") == "<sub>i</sub>"
    assert report_export._script_markup("ⁿ") == "<super>n</super>"
    assert report_export._script_markup("가") == ""
    data = report_export.to_pdf(
        "수식", [{"id": "s1", "heading": "방법", "content": "점수 = (Σᵢ wᵢ × sᵢ) / (Σᵢ wᵢ)"}]
    )
    text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    # Drawn as a lowered 「i」, not as a missing glyph.
    assert "Σi wi" in text.replace("\n", " ") or "Σ i w i" in text.replace("\n", " ")


def test_a_table_caption_goes_with_its_table():
    rows = "\n".join(f"| 방법 {i} | 한계 {i} |" for i in range(12))
    table = "| 방법 | 한계 |\n| --- | --- |\n" + rows
    # 25 and 26 short paragraphs put the caption on the last line of page 1.
    for paragraphs in (25, 26):
        filler = "\n\n".join("짧은 문단." for _ in range(paragraphs))
        content = f"{filler}\n\n**표 1. 기존 방법의 비교**\n\n{table}"
        data = report_export.to_pdf("표", [{"id": "s1", "heading": "동향", "content": content}])
        pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
        at = next(i for i, page in enumerate(pages) if "표 1." in page)
        assert "방법 0" in pages[at], (paragraphs, "the caption's page holds the table's first row")


def test_a_paragraph_does_not_break_after_vs():
    long = (
        "핵심 방법론을 서술하는 주장과 부차적 배경을 서술하는 주장의 가중치가 달라야 합니다. " * 3
        + "원문에서의 위치(초록·도입 vs. 본문·결론)와 문장 길이 등을 기준으로 가중치를 정합니다. "
        + "가중 평균 공식은 다음과 같습니다. " * 4
    )
    out = report.split_long_paragraphs(long)
    assert "vs. 본문·결론" in out
    assert "vs.\n" not in out
