"""10차 검증(LFLF2)의 기업 조사·경쟁사 분석: 본문의 인용과 답 아래 출처 목록이 서로 맞고,
인용 고침이 링크째 바뀌며, 쓴 사람이 목록에 적은 날짜가 남고, 조사 스킬은 검색을 더 한다."""

from __future__ import annotations

from app.routers import sessions
from app.services import (
    agent,
    post_checks,
    skill_packages,
    skill_scripts,
    skill_specs,
    value_guard,
)

sources = skill_scripts.module("source-check", "sources")
claims = skill_scripts.module("source-check", "claims")

URL2 = "https://thevc.kr/lilysai"
URL1 = "https://www.rebellions.ai/about"


def test_a_linked_citation_judged_wrong_goes_whole():
    # SK-company-3: 「[[3]](url)」 became 「[「출처 미확인」](url)」, the mark as a link label
    text = f"본사는 춘천이다 [[3]]({URL1})."
    found = [{"kind": "cite_mismatch", "line": 0, "start": text.index("[3]"),
              "end": text.index("[3]") + 3, "text": "[3]"}]
    assert sources.fix_cites(text, found) == "본사는 춘천이다 「출처 미확인」."
    plain = "본사는 춘천이다 [3]."
    found = [{"kind": "cite_mismatch", "line": 0, "start": 9, "end": 12, "text": "[3]"}]
    assert sources.fix_cites(plain, found) == "본사는 춘천이다 「출처 미확인」."


def test_a_citation_a_check_took_out_of_a_cell_is_not_given_back():
    # SK-company-2: the rewrite emptied the 출처 cell; the guard put 「[[2]]」 back
    before = (f"| 항목 | 내용 | 출처 |\n|---|---|---|\n| 설립 | 2020년 | [[2]]({URL2}) |\n"
              f"| 본사 | 서울 | [[1]]({URL1}) |")
    after = (f"| 항목 | 내용 | 출처 |\n|---|---|---|\n| 설립 | 2020년 |  |\n"
             f"| 본사 | 서울 | [[1]]({URL1}) |")
    settled = value_guard.settle(before, after, value_guard.Evidence.of(["2020년 서울"]))
    assert URL2 not in settled.text and URL1 in settled.text


def test_a_list_line_a_rewrite_lost_comes_back_and_a_citation_never_listed_goes():
    before = (f"본사는 서울이다 [[1]]({URL1}). 고객은 A사다 [[2]]({URL2}).\n\n### 출처\n"
              f"- [1] [회사 소개 · rebellions.ai]({URL1})\n- [2] [릴리스 · thevc.kr]({URL2})\n"
              "\n_참고 문구_")
    lost = (f"본사는 서울이다 [[1]]({URL1}). 고객은 A사다 [[2]]({URL2}). 투자는 B사다 "
            f"[[5]](https://example.com/x).\n\n### 출처\n- [1] [회사 소개 · rebellions.ai]({URL1})"
            "\n\n_참고 문구_")
    fixed = post_checks.cited_sources_listed(before, lost)
    assert f"- [2] [릴리스 · thevc.kr]({URL2})" in fixed
    assert "[[5]]" not in fixed and "투자는 B사다." in fixed
    assert fixed.rstrip().endswith("_참고 문구_")
    # nothing missing: unchanged
    assert post_checks.cited_sources_listed(before, before) == before


def test_the_writers_own_list_dates_stay_when_the_server_lists_the_sources():
    # SK-company-2: 「출처랑 날짜도」 asked; the server's list replaced the writer's dated one
    own = (f"## 출처\n[1] 회사 소개, 2026.3.12 확인, {URL1}\n"
           f"- [2] [릴리스 2025.09 투자]({URL2})\n")
    dates = agent._own_list_dates(own, [URL1, URL2])
    assert dates == {URL1: " · 2026-03-12"}


def test_a_material_named_in_a_cell_is_its_source():
    # SK-competitor-3: 「[경쟁 A 자료]」 cells marked 「(확인 필요)」 as unsourced
    table = ("| 항목 | 자사 | 경쟁 A |\n|---|---|---|\n"
             "| 회의록 | 자동 요약 [자사 자료] | 화자 구분 지원 [경쟁 A 자료] |")
    found = claims.check(table, "", {})
    assert not [f for f in found if f["kind"] == "unsourced_cell"]


def test_the_research_skills_search_deep_on_a_chat_turn():
    for key in ("company-research", "competitor-analysis"):
        spec = skill_specs.of_package(skill_packages.package_for(key))
        assert spec is not None and spec.process.get("deep_search") is True
        assert sessions._deep_search((key,), {key: spec})
    assert not sessions._deep_search(("copy-editing",), {})
