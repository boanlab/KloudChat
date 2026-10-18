"""One 출처 list per answer, and copied 「[[2]][[3]]」 marks resolved."""

from __future__ import annotations

from test_an_answer_cites_sources_by_number import _calls, _collect, _content, _final_text

from app.services import agent
from app.services.tools.base import Tool, ToolResult

_A11_T1 = (
    "- **의사결정 구조**: 정보화 부서가 기획·예산을 잡습니다 [4].\n\n"
    "## 출처\n\n"
    "[1] ALIO, 「주요 통계 — 임직원 현황」, 2026-06-말 기준, "
    "https://alio.go.kr/mobile/statistics/employeeStatues.do\n"
    "[2] JOB-ALIO, 「한국수자원공사 2026년 하반기 일반직 신입사원 공채」, 2026-09-23\n\n"
    "---\n\n"
    "**보완이 필요한 항목**: K-water 개별 정원·현원·예산 수치는 ALIO 기관별 공시 페이지에서 "
    "직접 확인해야 합니다."
)


def test_a_list_followed_by_a_paragraph_is_still_the_answers_own():
    at, end, numbers = agent._own_source_list(_A11_T1)
    assert _A11_T1[at:].startswith("## 출처") and numbers == [1, 2]
    assert _A11_T1[end:].strip().startswith("---")


async def test_the_server_list_replaces_it_and_the_paragraph_stays(monkeypatch):
    search = ("'K-water' 검색 결과:\n\n[1] 통계\nhttps://alio.go.kr/a\n요약\n\n"
              "[2] 공고\nhttps://job.alio.go.kr/b\n요약\n")

    async def run(_args):
        return ToolResult(content=search)

    tools = [Tool(name="web_search", description="웹 검색", parameters={"type": "object"},
                  run=run, label="웹 검색")]
    answer = [_content(_A11_T1.replace("[4]", "[2]")), "data: [DONE]"]
    _, events = await _collect(monkeypatch, [_calls("web_search", {"query": "K-water"}), answer],
                               tools)
    text = _final_text(events)
    assert text.count("출처\n") == 1
    assert "**보완이 필요한 항목**" in text
    assert text.index("**보완이 필요한 항목**") < text.index("### 출처")


def test_a_bare_double_bracket_citation_is_linked():
    sources = ["https://a.example/1", "https://b.example/2", "https://c.example/3"]
    linked, cited = agent._link_citations(
        "기준금리가 3.00%이고 11월 추가 인상 가능성이 열려 있으므로 [[2]][[3]], 지금 묶으면",
        sources)
    assert "[[2]](https://b.example/2)[[3]](https://c.example/3)" in linked
    assert cited == [2, 3]
    # a link the writer made stays as it is
    kept, _ = agent._link_citations("값 [[1]](https://a.example/1).", sources)
    assert kept == "값 [[1]](https://a.example/1)."
