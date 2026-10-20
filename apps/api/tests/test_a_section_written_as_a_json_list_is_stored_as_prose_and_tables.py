"""A section the writer sent back as a JSON list (「[{"section", "body", "tables",
"figures":}]」, a key left without a value) is stored as its prose and Markdown tables, so its
citations are credited and renumbered with the rest."""

from __future__ import annotations

import time

from app.services import citations, report

ANSWER = '''```json
[
  {
    "section": "6. 자료 수집 도구 및 측정 항목",
    "body": "정확도는 평가 집합 1,200건에 대해 정답 일치율로 측정한다[6].\\n\\n지연시간은 폐쇄형 1.8초, BM25 RAG 3.1초로 보고되었다[6].",
    "tables": [
      {
        "caption": "표: 측정 항목·산출 방식",
        "headers": ["측정 항목", "단위"],
        "rows": [["정답 일치율(EM)", "%"], ["허구 인용 비율", "%[6]"]]
      }
    ],
    "figures":
  }
]
```'''


def test_a_json_list_section_becomes_its_body_and_a_markdown_table():
    body = report.unwrap_json_prose(ANSWER)
    assert body == (
        "정확도는 평가 집합 1,200건에 대해 정답 일치율로 측정한다[6].\n\n"
        "지연시간은 폐쇄형 1.8초, BM25 RAG 3.1초로 보고되었다[6].\n\n"
        "표: 측정 항목·산출 방식\n\n| 측정 항목 | 단위 |\n|---|---|\n"
        "| 정답 일치율(EM) | % |\n| 허구 인용 비율 | %[6] |")
    # an object with tables reads the same; prose and other JSON are left as they were
    assert report.unwrap_json_prose(
        '{"body": "본문[1].", "tables": [{"headers": ["a"], "rows": [["1"]]}]}'
    ) == "본문[1].\n\n| a |\n|---|\n| 1 |"
    for kept in ("[담당자]", "[1, 2]", "본문이다 [1]."):
        assert report.unwrap_json_prose(kept) == kept


def test_its_citations_then_follow_the_renumbered_list():
    sources = [{"ordinal": n, "title": f"Work {n}", "url": f"https://example.org/{n}"}
               for n in range(1, 6)]
    sources.append({"ordinal": 6, "title": "t3_paper.pdf", "attached": True})
    sections = [{"id": "a", "heading": "배경", "content": "앞선 연구가 있다 [2]."},
                {"id": "b", "heading": "6. 자료", "content": report.unwrap_json_prose(ANSWER)}]
    out, kept = citations.cite_order(sections, sources)
    assert [s["title"] for s in kept] == ["Work 2", "t3_paper.pdf"]
    assert "[6]" not in out[1]["content"] and out[1]["content"].count("[2]") == 3


def test_a_long_unclosed_answer_finishes_quickly():
    text = "```json\n[" + " " * 50_000 + '"figures":' + " " * 50_000
    began = time.perf_counter()
    report.unwrap_json_prose(text)
    report.unwrap_json_prose("[" + " " * 50_000 + "]")
    assert time.perf_counter() - began < 0.5
