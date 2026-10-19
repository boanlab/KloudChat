"""An attached paper is cited by its own number and listed; a document noun named as a topic
or a source picks no 서식; a searched market study asks for no material."""

from __future__ import annotations

from app.services import citations, doc_formats, report

MARKET_STUDY = (
    "국내 AI 회의록(회의 녹취 요약) 서비스 시장 조사 보고서를 만들어줘. 우리 스타트업이 이 시장에 "
    "진입할지 판단하려고 해. 시장 규모와 성장, 주요 경쟁 서비스(네이버 클로바노트, 다글로, "
    "비토 등), 가격, 차별화 기회, 리스크를 포함하고 최신 자료 출처를 달아줘.")

WEB = {"ordinal": 2, "title": "ARGUS: Retrieval-Augmented QA System for Government Services",
       "author": "Song Jiang, Xiaofeng Xie", "year": "2025", "origin": "web"}
JUDGE = {"ordinal": 4, "title": "LLM-as-a-Judge: Rapid Evaluation of Legal Document "
         "Recommendation", "author": "A Pradhan, A Ortan, A Verma 외", "year": "2025",
         "origin": "web"}
PAPER = {"ordinal": 5, "title": "kim_park_2025.pdf", "origin": "file", "attached": True,
         "originLabel": "첨부 파일"}


def _section(heading: str, content: str) -> dict:
    return {"id": heading, "heading": heading, "level": 1, "status": "done", "content": content}


# ── 서식 by what is asked ─────────────────────────────────────────────


def test_a_market_study_of_a_minutes_service_gets_no_minutes_head() -> None:
    assert doc_formats.detect(MARKET_STUDY) is None
    assert "Genre: 회의록" not in report._genre_rule(MARKET_STUDY)


def test_minutes_asked_for_are_still_minutes() -> None:
    assert doc_formats.detect("어제 회의 녹취로 회의록 써 줘").id == "minutes"
    assert doc_formats.detect("회의록 앱 출시 보도자료 초안 써줘").id == "press"
    # the source it draws on is no order
    assert doc_formats.detect("지난 회의록을 바탕으로 결과 보고서를 써 줘").id == "result_report"


def test_a_searched_market_study_is_not_asked_for_material() -> None:
    assert not report._results_without_data(MARKET_STUDY, [], web=True)
    # an upload the ask points at is still asked for
    assert report._results_without_data("첨부 파일의 녹취로 시장 동향을 조사해 줘", [], web=True)


def test_a_press_outline_keeps_no_title_or_lead_sections() -> None:
    assert report.press_headings(["제목", "부제", "리드", "본문", "노바랩스 소개", "문의"]) == [
        "본문", "노바랩스 소개", "문의"]
    assert report.press_headings(["제목", "리드"]) == ["본문"]


# ── attached paper as a source ─────────────────────────────────────────


def test_figures_only_the_attached_paper_holds_are_credited_to_it() -> None:
    sections = [_section("선행연구", "Kim과 Park(2025)은 정확도 38.2%, BM25 59.6%를 보고했다 [2]. "
                         "ARGUS는 정부 질의응답에 검색을 붙였다 [2].")]
    texts = citations.source_texts(
        [WEB, JUDGE, PAPER],
        "[2] ARGUS\n출처: https://doi.org/x\n본문 발췌:\nretrieval accuracy improved\n\n"
        "[4] LLM-as-a-Judge\n출처: https://arxiv.org/abs/1\n본문 발췌:\njudges",
        ["# 이번 요청에 첨부된 파일\n\n## kim_park_2025.pdf\nRAG 38.2% vs BM25 59.6%"])
    out = citations.credit_attachments(sections, [WEB, JUDGE, PAPER], texts)
    assert out[0]["content"] == ("Kim과 Park(2025)은 정확도 38.2%, BM25 59.6%를 보고했다 [5]. "
                                 "ARGUS는 정부 질의응답에 검색을 붙였다 [2].")


def test_a_figure_the_web_page_holds_keeps_its_citation() -> None:
    sections = [_section("배경", "검색 정확도는 38.2%였다 [2].")]
    texts = {2: "[2] ARGUS\n… 38.2% …", 5: "## kim_park_2025.pdf\n38.2%"}
    assert citations.credit_attachments(sections, [WEB, PAPER], texts) == sections


def test_an_uncited_attached_paper_is_still_listed() -> None:
    sections = [_section("배경", "ARGUS가 있다 [2].")]
    _, kept = citations.cite_order(sections, [WEB, PAPER])
    assert [(s["ordinal"], s["title"]) for s in kept] == [(1, WEB["title"]), (2, PAPER["title"])]


def test_the_writers_reference_lines_restating_sources_leave_no_orphans() -> None:
    sections = [
        _section("본문", "근거 [4]."),
        _section("참고문헌", "[4] Pradhan A., Ortan A., Verma A., Seshadri M. (2025).\n\n"
                 "[5] Yang B., Yu X., Zheng X. (2024)."),
    ]
    out = citations.drop_restated_references(sections, [WEB, JUDGE, PAPER])
    assert [s["heading"] for s in out] == ["본문"]
    # by first author and year, numbering aside
    restated = [_section("참고문헌", "- Pradhan, A. 외 (2025). 법률 문서 평가")]
    assert citations.drop_restated_references(restated, [JUDGE]) == []
