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
    # LFLF5: the test account's copy had no stored fields, so the turn's specs lacked it
    assert sessions._deep_search(("competitor-analysis",), {})


# ── LFLF3: searched what the request lists, a cell's own source, opportunities on unknowns ──

COMPETITORS = ("국내 업무용 협업툴 경쟁사 비교표 만들어 줘. 잔디, 플로우, 네이버웍스를 대상으로 "
               "포지셔닝, 주요 기능, 가격(요금제) 정리하고 출처 달아 줘.")


def test_the_names_a_request_lists_are_read_from_the_ask_only():
    from app.services import query_terms

    names = [n for n, _ in query_terms.listed_names(COMPETITORS)]
    assert names == ["잔디", "플로우", "네이버웍스"]
    two = ("우리 제품은 사내 설치형이야(자체 GPU 서버, 외부 반출 없음). 경쟁사로 "
           "뤼튼 엔터프라이즈, 네이버 하이퍼클로바X(기업용), 마이크로소프트 365 Copilot을 "
           "놓고 분석해 줘.")
    assert [n for n, _ in query_terms.listed_names(two)] == [
        "뤼튼 엔터프라이즈", "네이버 하이퍼클로바X", "마이크로소프트 365 Copilot"]
    assert query_terms.unsearched(two, ["뤼튼 요금", "하이퍼클로바X 가격", "MS 365 Copilot"]) == []
    # a pasted memo's lines are material, not the ask
    memo = "더존비즈온 조사해 줘.\n\n- ERP 1위, 본사 춘천, 공공 클라우드"
    assert query_terms.listed_names(memo) == []
    assert query_terms.unsearched(COMPETITORS, ["잔디 JANDI 요금제", "플로우 flow"]) == [
        "네이버웍스"]


def test_another_competitors_citation_does_not_source_a_cell():
    table = ("| 항목 | 잔디 | 플로우 | 출처 |\n|---|---|---|---|\n"
             "| 핵심 기능 | 채팅, 파일 공유 [1] | 프로젝트 관리, 태스크 할당 | 확인 필요 |")
    found = claims.check(table, "", {})
    assert [(f["kind"], f["column"]) for f in found] == [("unsourced_cell", "플로우")]


def test_an_opportunity_resting_on_an_unknown_is_labelled_a_guess():
    text = ("| 항목 | 자사 | 뤼튼 | HCX |\n|---|---|---|---|\n"
            "| 사내 설치 | 있음(자사 자료) | 확인 필요 | 확인 필요 |\n"
            "| 가격 | 확인 필요 | 월 1만 원 [1] | 확인 필요 |\n\n"
            "## 차별화 기회\n"
            "1. 설치형 전용 제안 — 근거: 비교표 「사내 설치」 행.\n"
            "2. 반출 금지 기관 — 뤼튼의 설치 여부가 확인되지 않으므로 자사만 충족.\n"
            "3. 가격 비교 — 근거: 비교표 「가격」 행 [1].\n"
            "4. 양식 맞춤 — 경쟁사는 확인 필요이므로 가설로 둠, 검증: 블라인드 비교.")
    found = claims.opportunities_on_unknowns(text.split("\n"))
    assert [f["text"].split(" — ")[0] for f in found] == ["설치형 전용 제안", "반출 금지 기관"]
    fixed = claims.fix(text, found)
    assert "행. (가설)" in fixed and "자사만 충족. (가설)" in fixed
    assert "「가격」 행 [1]." in fixed and "[1]. (가설)" not in fixed


def test_the_whole_ask_as_one_query_does_not_count_as_searching_each_name():
    from app.services import query_terms

    # LFLF4 SK-competitor-1: the first query was the request itself
    whole = COMPETITORS[:60]
    assert query_terms.unsearched(COMPETITORS, [whole, "잔디 요금제", "플로우 요금제"]) == [
        "네이버웍스"]


def test_the_persons_own_product_in_their_words_is_not_marked():
    # LFLF4 SK-competitor-2: 14 own-product facts marked 「(확인 필요)」
    material = "우리 제품은 사내 설치형 AI 챗봇이야(자체 GPU 서버, 문서 기반 답변, 보고서 생성)."
    table = ("| 항목 | 자사 | 뤼튼 |\n|---|---|---|\n"
             "| 설치 | 지원 (자체 GPU 서버) | 확인 필요 |\n"
             "| 기능 | 문서 기반 답변, 보고서 생성 | 확인 필요 |\n"
             "| 가격 | 월 1만 원 최저가 | 확인 필요 |")
    found = claims.check(table, material, {})
    assert [(f["row"], f["column"]) for f in found] == [("가격", "자사")]


def test_a_page_profiling_another_company_is_not_a_one_pagers_source():
    # LFLF6 SK-company-2: THE VC's 앳 page cited for 리벨리온's series C investors
    text = ("## 리벨리온(Rebellions) 한눈에\n\n"
            "시리즈C에 산업은행이 참여했습니다 [[2]](https://thevc.kr/at-1). "
            "설립은 2020년입니다 [[1]](https://thevc.kr/rebellions).\n\n"
            "- [1] [리벨리온 - 기업정보 | 투자 - THE VC · thevc.kr](https://thevc.kr/rebellions)\n"
            "- [2] [앳(에이스) - 기업정보 | 투자 - THE VC · thevc.kr](https://thevc.kr/at-1)")
    found = [f for f in sources.check(text, "", {}) if f["kind"] == "cite_mismatch"]
    assert [f["text"] for f in found] == ["[2]"] and "앳" in found[0]["why"]
    fixed = sources.fix_cites(text, found)
    assert "참여했습니다 「출처 미확인」." in fixed
    assert "[[1]](https://thevc.kr/rebellions)" in fixed
    # an answer that is not a company's one-pager is not judged so
    assert not [f for f in sources.check(text.replace("한눈에", "투자 동향"), "", {})
                if f["kind"] == "cite_mismatch"]


def test_a_cell_whose_only_source_is_about_another_competitor_is_unsourced():
    # LFLF6 SK-competitor-1: a JANDI blog the row's only source, a 네이버웍스 fact beside it
    text = ("대상: 잔디(JANDI), 플로우(Flow), 네이버웍스(Naver Works)\n\n"
            "| 항목 | 잔디 | 플로우 | 네이버웍스 | 출처 |\n|---|---|---|---|---|\n"
            "| 최근 움직임 | 연동 확대 | 확인 필요 | 클로바노트 별도 상품 | "
            "[[4]](https://blog.jandi.com/connect) |\n"
            "| 요금 | 확인 필요 | 확인 필요 | Lite 4,000원 | "
            "[[3]](https://naver.worksmobile.com/pricing/) |\n"
            "| 기능 | 메신저 [[4]](https://blog.jandi.com/connect) | 칸반 "
            "[[5]](https://daouoffice.com/blog/jandi-flow-compare) | 결재 | 확인 필요 |\n\n"
            "- [4] [JANDI Connect 연동](https://blog.jandi.com/connect)\n"
            "- [3] [요금제 | NAVER WORKS](https://naver.worksmobile.com/pricing/)\n"
            "- [5] [잔디 vs 플로우 비교](https://daouoffice.com/blog/jandi-flow-compare)")
    found = [f for f in claims.check(text, "", {}) if f["kind"] == "unsourced_cell"]
    assert [(f["row"], f["column"]) for f in found] == [
        ("최근 움직임", "네이버웍스"), ("기능", "네이버웍스")]
    assert "잔디" in found[0]["why"] and "다른 회사" in found[0]["why"]


def test_memo_lines_handed_over_to_check_are_each_searched():
    from app.services import query_terms

    # LFLF6 SK-company-3: 「ERP 1위」 and 「공공 클라우드」 never searched
    memo = ("아래 메모로 더존비즈온 기업 조사 한 장 만들어 줘.\n\n[영업팀 메모]\n"
            "- 더존비즈온, ERP 1위 업체라고 들음\n- 본사 강원도 춘천?\n"
            "- 최근 AI 넣은 ERP 냈다고 함 (이름 모름)\n- 공공 클라우드 사업도 한다고 함")
    assert [w for _, w in query_terms.memo_items(memo)][0] == ["ERP", "1위"]
    queries = ["더존비즈온 AI ERP", "더존비즈온 본사 춘천", "더존비즈온 2025 실적"]
    assert query_terms.unsearched(memo, queries) == [
        "더존비즈온, ERP 1위 업체라고 들음", "공공 클라우드 사업도 한다고 함"]
    more = ["더존 ERP 점유율 1위", "더존 공공 클라우드"]
    assert query_terms.unsearched(memo, [*queries, *more]) == []
    # the person's own material to work from is not a list of things to look up
    assert query_terms.memo_items("이 자료만으로 정리해 줘.\n\n- 요금 월 6천 원\n- 고객 30곳") == []


def test_a_judged_statement_is_read_against_the_pages_it_cites():
    import asyncio
    import json

    # LFLF7 SK-company-1: 「AWS」 in another page kept a statement its own [1] lacks
    one, two = "https://smartbizn.com/a", "https://news.example/b"
    answer = (f"노마디안은 AWS와 2025년 협약했다 [[1]]({one}).\n"
              f"매출은 1,753억 원이다 [[2]]({two}).")
    computed = [f"[1] 한컴 AI 매출\n{one}\n한컴은 AI 문서 도구를 낸다.",
                f"[2] 한컴 실적\n{two}\n노마디안은 AWS와 2025년 협약했고 매출 1,753억 원을 냈다."]
    judged = [{"quote": "노마디안은 AWS와 2025년 협약했다", "why": "자료에 없음"},
              {"quote": "매출은 1,753억 원이다", "why": "자료에 없음"}]

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        if prompt.startswith("You check an answer"):
            return json.dumps({"unsupported": judged}, ensure_ascii=False), {}
        if prompt.startswith("Below are the material an answer was written from"):
            return json.dumps({"verdicts": []}), {}
        return answer.replace("노마디안은 AWS와 2025년 협약했다 ", ""), {}

    pages = post_checks.cited_pages("노마디안은 AWS와 2025년 협약했다", answer, computed)
    assert pages.startswith("[1] 한컴 AI 매출")
    out = asyncio.run(post_checks.run(
        answer, keys=["company-research"], request="한컴 조사해 줘", material="", attached="",
        computed=computed, today="2026-10-16", complete=complete, model="w", judge="j",
        api_key="k"))
    sent = [row["text"] for row in out.sent]
    assert "노마디안은 AWS와 2025년 협약했다" in sent and "매출은 1,753억 원이다" not in sent
    # a page not read this turn (or no citation) leaves the reading to all the evidence
    assert post_checks.cited_pages("매출은 1,753억 원이다", answer, computed[:1]) is None


def test_a_cell_citing_another_product_of_the_same_name_is_unsourced():
    # LFLF6 SK-competitor-2: GitHub Copilot pages cited in the M365 Copilot column
    ms = "https://learn.microsoft.com/m365/copilot"
    text = ("| 항목 | 뤼튼 | M365 Copilot |\n|---|---|---|\n"
            f"| 대상 | 확인 필요 | 조직 [[1]]({ms}) |\n"
            f"| 배포 | 확인 필요 | 클라우드 [[2]](https://blogs.microsoft.com/m365) |\n"
            "| 고객 | 확인 필요 | Duolingo·FedEx [[3]](https://github.com/features/copilot) |\n"
            "| 앱 | 확인 필요 | Microsoft Copilot 앱 "
            "[[4]](https://www.microsoft.com/copilot) |\n\n"
            f"- [1] [Microsoft 365 Copilot overview · learn.microsoft.com]({ms})\n"
            "- [2] [Introducing Microsoft 365 Copilot · blogs.microsoft.com]"
            "(https://blogs.microsoft.com/m365)\n"
            "- [3] [GitHub Copilot · Your AI coding agent · github.com]"
            "(https://github.com/features/copilot)\n"
            "- [4] [Download Microsoft Copilot App · microsoft.com](https://www.microsoft.com/copilot)")
    found = [f for f in claims.check(text, "", {}) if f["kind"] == "unsourced_cell"]
    assert [(f["row"], f["column"]) for f in found] == [("고객", "M365 Copilot")]
    assert "GitHub Copilot" in found[0]["why"]


def test_a_mark_stays_on_a_value_its_cited_page_does_not_give():
    # LFLF8 SK-company-3: 2분기 figures cited to a darts-match page lost their marks
    darts, dart = "https://darts.example/match", "https://dart.fss.or.kr/report"
    before = (f"2분기 매출은 1,031억 원이다 [[8]]({darts}).\n\n"
              f"영업이익은 260억 원이다 [[2]]({dart}).")
    after = before.replace("1,031억 원이다", "1,031억 원(확인 필요)이다").replace(
        "260억 원이다", "260억 원(확인 필요)이다")
    ev = value_guard.Evidence.of([
        f"[8] 다트 경기 결과\n{darts}\n결승은 3대 1로 끝났다.",
        f"[2] 반기보고서\n{dart}\n매출 1,031억 원, 영업이익 260억 원."], answer=before)
    settled = value_guard.settle(before, after, ev).text
    assert "1,031억 원(확인 필요)" in settled and "260억 원이다" in settled


def test_a_comparison_page_and_a_korean_maker_are_not_another_product():
    # LFLF8: 「GitHub Copilot and Microsoft 365 Copilot」 and 「마드라스체크(플로우)」
    ms = "https://learn.microsoft.com/m365/copilot"
    text = ("| 항목 | 플로우 | 365 Copilot |\n|---|---|---|\n"
            f"| 대상 | 확인 필요 | 조직 [[1]]({ms}) |\n"
            f"| 배포 | 확인 필요 | 클라우드 [[1]]({ms}) |\n"
            "| 가격 | 세미나 [[3]](https://thevc.kr/madrascheck) | 월 30달러 "
            "[[6]](https://gptprompts.ai/copilot-pricing) |\n\n"
            f"- [1] [Microsoft 365 Copilot overview · learn.microsoft.com]({ms})\n"
            "- [3] [마드라스체크(플로우) - 기업정보 - THE VC · thevc.kr]"
            "(https://thevc.kr/madrascheck)\n"
            "- [6] [Copilot Pricing 2026: GitHub Copilot and Microsoft 365 Copilot Cost · "
            "gptprompts.ai](https://gptprompts.ai/copilot-pricing)")
    assert not [f for f in claims.check(text, "", {}) if "다른 제품" in f["why"]]
