"""A percentage stands only when the material gives it or works it out from its figures (a share
of two figures of one measure, a gap of two given percentages); one worked from unrelated
numbers (「82% of frontier」 → a cloud 「100%」, 20 pages against 40 tasks) is marked, in a deck
made or edited and in a report, and one the deck or report already showed stays. A weekday a
next-week plan never had is found though this week's memo line names the same customer, and a
limit the memo only asks about is not written as one the team reviewed."""

from __future__ import annotations

import time

import pytest

from app.pipeline import check_stages, revise_stages
from app.pipeline.stage import Context
from app.services import report, revise, stated_days, stated_policy, value_guard

ED1 = ("Create a 8-slide English presentation for a conference talk: 'Can small on-premise "
       "language models replace cloud APIs for document drafting?' Findings: a 27B local model "
       "scored 82% of a frontier cloud model on a 40-task drafting benchmark (rated by 3 "
       "reviewers), cost per 1,000 documents was $4 vs $38, latency 9s vs 14s, but failed more "
       "on long-context tasks (64% vs 91% above 20 pages). Recommendation: hybrid routing.")
SALES = "3분기 매출 128억 원, 수도권 71억 원, 영남 33억, 영업이익 9.6억 원, 전분기 영업이익률 6.8%"


def test_a_percentage_counts_when_given_or_worked_from_figures_of_one_measure():
    basis = value_guard.percent_basis([ED1])
    assert all(basis.backs(v, raw) for v, raw in ((82, "82"), (64, "64"), (91, "91"),
                                                  (27, "27"), (18, "18")))
    # 20 pages against 40 tasks is no 100%, nor is 82% of the frontier
    assert not basis.backs(100, "100") and not basis.backs(50, "50")
    sales = value_guard.percent_basis([SALES])
    # 71억 of 128억, 9.6억 of 128억, 7.5% against 6.8%
    assert sales.backs(55.5, "55.5") and sales.backs(55, "55") and sales.backs(7.5, "7.5")
    assert sales.backs(0.7, "0.7", unit="%p") and not sales.backs(0.7, "0.7")
    assert not sales.backs(12, "12") and not sales.backs(56.5, "56.5")
    # a day is no figure: 「9.6억」 is not 9월 6일
    assert value_guard.percent_basis(["9.6억 원, 128억 원"]).backs(7.5, "7.5")


def test_a_deck_marks_an_unstated_percentage_and_keeps_a_worked_share():
    slides = [
        {"id": "a", "layout": "table", "title": "Benchmark",
         "rows": [["Metric", "Local 27B", "Cloud"], ["Pass rate", "82%", "100%"],
                  ["Long context", "64%", "91%"]]},
        {"id": "b", "layout": "bullets", "title": "Accuracy",
         "bullets": ["Accuracy: 82% vs 100% on drafts", "64% vs 91% above 20 pages"]},
    ]
    out, count = check_stages.mark_unbacked_percents(slides, [ED1])
    assert count == 2
    assert out[0]["rows"][1] == ["Pass rate", "82%", "100% (확인 필요)"]
    assert out[0]["rows"][2] == ["Long context", "64%", "91%"]
    assert out[1]["bullets"] == ["Accuracy: 82% vs 100%(확인 필요) on drafts",
                                 "64% vs 91% above 20 pages"]
    korean = [{"id": "c", "layout": "bullets", "title": "지역별 매출",
               "bullets": ["수도권 71억 원(55.5%)", "영업이익률 7.5%, 0.7%p 개선",
                           "수도권이 전체의 약 56%"]}]
    assert check_stages.mark_unbacked_percents(korean, [SALES]) == (korean, 0)
    # 56% said flatly is a place off the worked 55.47%; the base others are a share of stands
    flat = [{"id": "d", "layout": "bullets", "title": "t", "bullets": ["수도권 56%"]}]
    assert check_stages.mark_unbacked_percents(flat, [SALES])[1] == 1
    base = [{"id": "e", "layout": "table", "title": "Benchmark",
             "rows": [["Model", "Score"], ["Cloud", "100% (baseline)"], ["Local", "82%"]]}]
    assert check_stages.mark_unbacked_percents(base, [ED1]) == (base, 0)


def test_a_chart_point_no_figure_backs_is_handled_as_an_untraced_one():
    chart = {"id": "c", "layout": "chart", "title": "Pass rate",
             "chart": {"type": "bar", "unit": "%", "categories": ["Local", "Cloud", "Long"],
                       "series": [{"name": "Pass rate", "values": [82, 100, 64]}]}}
    weak, count = check_stages.mark_unbacked_percents([chart], [ED1])
    assert count == 1 and weak[0]["chart"]["categories"] == ["Local", "Long"]
    assert weak[0]["chart"]["series"][0]["values"] == [82, 64]
    strong, _ = check_stages.mark_unbacked_percents([chart], [ED1], strong=True)
    assert strong[0]["chart"]["categories"] == ["Local", "Cloud (확인 필요)", "Long"]


def test_an_edit_marks_only_percentages_new_to_the_deck():
    before = "\n".join(["Accuracy: 82% vs 100% on drafts", "64% vs 91%"])
    slides = [{"id": "a", "layout": "table", "title": "Benchmark results",
               "rows": [["Metric", "Local", "Cloud"], ["Pass rate", "82%", "100%"],
                        ["Gap", "18%", "73%"]]}]
    changed = check_stages.edited_percents(slides, [0], request=ED1, material=[],
                                           before=before)
    # 100% the deck already showed stays; 73% is new and backed by nothing
    assert changed == [0]
    assert slides[0]["rows"][1] == ["Pass rate", "82%", "100%"]
    assert slides[0]["rows"][2] == ["Gap", "18%", "73% (needs checking)"]
    # a deck made from no figures of the person's is not checked
    plain = [{"id": "a", "layout": "bullets", "title": "Market", "bullets": ["Share 40%"]}]
    assert check_stages.edited_percents(plain, [0], request="Make a deck on LLMs",
                                        material=[], before="") == []


ED1_SLIDES = [
    {"id": "a", "layout": "title", "title": "Small On-Premise LLMs vs Cloud APIs for Drafting"},
    {"id": "b", "layout": "bullets", "title": "Benchmark Design",
     "bullets": ["40-task drafting benchmark", "3 reviewers rate output"]},
    {"id": "c", "layout": "metrics", "title": "The Long-Context Gap",
     "metrics": [["82%", "Accuracy"], ["91%", "Long-ctx success"]]},
]


@pytest.mark.asyncio
async def test_a_slide_rewrite_that_brings_in_an_unstated_percentage_is_marked(monkeypatch):
    async def rewrite_slide(**_kwargs):
        return {"id": "c", "layout": "table", "title": "The Long-Context Gap",
                "rows": [["Metric", "Local", "Cloud"], ["Accuracy", "82%", "100%"],
                         ["Long context", "64%", "91%"]]}, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(revise_stages.deck_service, "rewrite_slide", rewrite_slide)
    typed = "Make slide 3 a table comparing local and cloud"
    bundle = revise_stages.bundle_for(
        request=ED1, instruction=typed, model={"id": "m"}, planner=None, api_key="k",
        is_deck=True, title="LLMs", data={}, parts=[dict(s) for s in ED1_SLIDES], material=[],
        plan=revise.Plan(scope="parts", targets=[2], note=typed), format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})
    bundle.plan.order = [2]
    events = [e async for e in revise_stages.Rewrite().run(bundle, Context())]
    rows = bundle.document.sections[2]["rows"]
    assert rows[1] == ["Accuracy", "82%", "100% (needs checking)"]
    assert rows[2] == ["Long context", "64%", "91%"]
    assert events[-2]["type"] != "slide" or "needs checking" in str(events[-2]["slide"])


def test_a_report_marks_an_unstated_percentage_and_keeps_worked_ones():
    text = ("수도권 매출은 71억 원으로 전체의 55.5%다. 영업이익률은 7.5%로 0.7%p 올랐다. "
            "시장 점유율은 12%다.\n\n| 구분 | 비중 |\n|---|---|\n| 수도권 | 55.5% |\n"
            "| 합계 | 100% |")
    marked = report._without_invented_percents(text, [SALES])
    assert "시장 점유율은 12%(확인 필요)다." in marked
    assert marked.count(value_guard.MARK) == 1
    assert report._without_invented_percents("값이 없다.", [SALES]) == "값이 없다."


def test_a_report_with_figures_marks_an_unstated_percentage_and_leaves_its_money_to_the_trace():
    from app.pipeline import report_stages

    text = "수도권 매출은 71억 원으로 55.5%이고, 점유율은 12%, 광고비는 3억 원이다."
    marked = report_stages._unbacked_marked(text, [SALES], computed="", grounded=True)
    assert marked == (
        "수도권 매출은 71억 원으로 55.5%이고, 점유율은 12%(확인 필요), 광고비는 3억 원이다.")
    loose = report_stages._unbacked_marked(text, [SALES], computed="", grounded=False)
    assert "3억 원(확인 필요)" in loose and "12%(확인 필요)" in loose

def test_a_report_edit_marks_a_new_percentage_and_keeps_the_ones_it_had():
    original = ["수도권 비중은 55.5%이며 목표 대비 103%다."]
    body = "수도권 비중은 55.5%, 목표 대비 103%, 이익률은 9%다."
    marked = revise_stages._figures_marked(body, SALES, [], {}, original)
    assert marked == "수도권 비중은 55.5%, 목표 대비 103%, 이익률은 9%(확인 필요)다."


def test_percentages_are_read_in_linear_time():
    started = time.monotonic()
    basis = value_guard.percent_basis([" " * 50000 + "1%", "1" + " " * 50000 + "page"])
    value_guard.mark_unbacked_percents(" " * 50000 + "5 percent" + " " * 50000, basis)
    assert time.monotonic() - started < 0.5


MEMO = (
    "- 화: 기존 거래처 '한솔마트' 재고 클레임 2건 처리 완료(반품 420만원).\n"
    "- 이슈: CRM 병합 완료는 다음주 수요일 예정.\n"
    "- 다음주 계획: 대성유통 단가 최종 협상(목요일 2차 미팅), 한솔마트 재고 점검 방문, CRM 병합 "
    "마무리.\n"
    "- 요청사항: 단가 인하 폭 결재 필요(최대 4%까지 허용 여부)."
)


def test_a_next_week_plan_is_not_put_on_the_day_this_weeks_line_named_its_customer():
    assert stated_days.untraced("| 화요일 | 한솔마트 재고 점검 방문 |", MEMO) == [
        "한솔마트 재고 점검 방문"]
    assert stated_days.untraced("다음 주 화요일에는 한솔마트 재고 점검을 방문한다.", MEMO)
    # this week's line itself, a next-week day the memo gives, and a plan on its own day stay
    assert not stated_days.untraced("| 화요일 | 한솔마트 재고 클레임 2건 처리 |", MEMO)
    assert not stated_days.untraced("| 수요일 | CRM 병합 마무리 |", MEMO)
    assert not stated_days.untraced("| 목요일 | 대성유통 단가 최종 협상 |", MEMO)


def test_a_limit_the_memo_asks_about_is_not_one_the_team_reviewed():
    said = stated_policy.untraced(
        "대성유통의 5% 인하 요구에 대해 팀 내에서 검토한 최대 허용 인하 폭이 4%입니다.", MEMO)
    assert said == ["최대 허용 인하 폭", "팀 내에서 검토한"]
    assert not stated_policy.untraced("최대 4%까지 인하를 허용할지 결재를 요청합니다.", MEMO)
    # a review the material reports stands
    assert not stated_policy.untraced("팀 내에서 검토한 결과 3%로 정했다.",
                                      "팀에서 검토 결과 3%로 확정")
