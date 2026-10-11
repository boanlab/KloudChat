"""Every value a finished document states traces to the material or to code, or it is blanked.

The fixtures are the lineup v1 acceptance runs (tests/fixtures/grounding): the request, the
material, what code computed for that run (the date-calc block, the quote totals, run_code's
output), and the document or answer as it was written. The invented values the reviewers
found are caught and blanked; the grounded ones in the same documents are left as written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import check_skills, skill_scripts

_FIXTURES = Path(__file__).parent / "fixtures" / "grounding"
_TODAY = "2026-10-08"


def _run(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _texts(run: dict) -> list[str]:
    return [s["content"] for s in run["sections"]] if run["kind"] == "report" else [run["answer"]]


def _options(run: dict) -> dict:
    # The strictness an agent's row holds (`check_options`), seeded from its file, and the
    # one its format's 서식 sets (a career history traces titles too).
    from app.services import design_templates, starter

    settings = dict((starter.agent_spec(run["facts"]) or {}).get("check_options") or {})
    template = design_templates.for_format(run["facts"])
    settings |= {k: True for k in (template.check_options if template else ())}
    return check_skills.options_for(None, settings) | {"today": _TODAY}


def _found(run: dict) -> list[dict]:
    trace = skill_scripts.entry("grounding-check")
    out = []
    for text in _texts(run):
        material = f"{run['request']}\n{run['material']}"
        out += trace.check(text, material, run["computed"], _options(run))
    return out


def _repaired(run: dict) -> str:
    trace = skill_scripts.entry("grounding-check")
    material = f"{run['request']}\n{run['material']}"
    out = []
    for text in _texts(run):
        out.append(trace.fix(text, trace.check(text, material, run["computed"], _options(run))))
    return "\n".join(out)


#: run → (invented values the review found, grounded values written beside them).
_INVENTED = {
    # 「11월 초까지 오지오가 서버를 우선 사용」: the recording says 「다음 주까지」.
    "minutes-2__r2": (["11월 초"], ["2026-10-21(수)", "2026-10-19", "11월 20일"]),
    # A meeting day the memo never gives.
    "minutes-3__r1": (["2026년 10월 8일"], ["다음 주 금요일"]),
    # 「참가 인원은 120명」 out of 「다과 120명분」.
    "minutes-5__r2": (["120명"], ["20만 원", "10/23"]),
    # 「오전 9시 이전 예약의 노쇼율」: the memo has 12,000 bookings and 12.4% only.
    "career-docs-1__r2": (["오전 9시"], ["12.4%", "1만 2천 건"]),
    "client-proposal-1__r1": (["3명", "1.5명"], ["47,100,000", "4,710,000", "51,810,000", "3.0MM"]),
    "client-proposal-1__r2": (["3명", "1.5명"], ["47,100,000", "51,810,000"]),
    # The first of two answers: numbers no run_code printed (198 rows, 3.43 vs 3.44, +0.34).
    "data-analysis-5__r2": (["198행", "3.43", "3.44", "+0.34", "2.96", "55"],
                            ["253행", "3.469", "3.303", "-0.136", "+0.166", "246행"]),
}
#: The schedule dates the proposal invented inside the RFP's period (the agent's rule: dates the
#: material does not give are 「협의」) — ungrounded by the same rule, not false positives.
_ALSO_UNGROUNDED = {
    "client-proposal-1__r1": {"2026. 12. 1.", "2027. 1. 1."},
    "client-proposal-1__r2": {"2026. 11. 15.", "2026. 11. 16.", "2026. 12. 20.", "2026. 12. 21.",
                              "2027. 1. 15.", "2027. 1. 16."},
    "career-docs-1__r2": {"6개월"},
    "data-analysis-5__r2": {"195행", "55행", "191행", "3.41", "136", "14", "43", "3.26", "46",
                            "3.29", "31", "3.58", "16", "3.67"},
}


@pytest.mark.parametrize("name", sorted(_INVENTED))
def test_an_invented_value_is_caught_and_blanked(name):
    run = _run(name)
    invented, grounded = _INVENTED[name]
    found = {f["text"] for f in _found(run)}
    assert set(invented) <= found
    repaired = _repaired(run)
    for value in grounded:
        assert value in repaired, f"{value} 는 자료·계산값에 있는데 지워졌습니다"


@pytest.mark.parametrize("name", sorted(_INVENTED))
def test_nothing_else_in_those_documents_is_blanked(name):
    """The false positives on the same runs' grounded sentences: none."""
    run = _run(name)
    allowed = set(_INVENTED[name][0]) | _ALSO_UNGROUNDED.get(name, set())
    assert {f["text"] for f in _found(run)} <= allowed


@pytest.mark.parametrize("name", [
    "minutes-1__r1", "minutes-4__r2", "gaejo-report-1__r1", "gaejo-report-2__r2",
    "comparison-report-1__r1", "client-proposal-5__r1", "career-docs-5__r1", "data-analysis-5__r1",
])
def test_a_grounded_document_is_left_as_written(name):
    run = _run(name)
    assert _found(run) == []
    assert _repaired(run) == "\n".join(_texts(run))


def test_the_blank_takes_the_value_s_place():
    minutes = _repaired(_run("minutes-2__r2"))
    assert "[시기]까지 오지오가 서버를 우선 사용" in minutes
    career = _repaired(_run("career-docs-1__r2"))
    assert "[시각] 이전 예약의 노쇼율" in career
    proposal = _repaired(_run("client-proposal-1__r1"))
    assert "AI 엔지니어 [수치](3.0MM)" in proposal
    analysis = _repaired(_run("data-analysis-5__r2"))
    assert "원본 [수치]" in analysis and "원본 253행" in analysis


@pytest.mark.parametrize("name", ["career-docs-2__r1", "career-docs-2__r2"])
def test_work_with_a_tool_the_material_only_lists_is_dropped(name):
    """「AWS 위에서 배포·운영을 수행했습니다」: the material lists AWS under 기술 only."""
    run = _run(name)
    found = _found(run)
    assert [f["kind"] for f in found] == ["claim"]
    repaired = _repaired(run)
    assert "AWS" not in repaired.replace("Elasticsearch, AWS를 활용합니다", "")
    # The facts around it stay: periods, metrics, the stack list itself.
    assert "820ms" in repaired and "310ms" in repaired


def test_arithmetic_of_grounded_values_is_grounded():
    trace = skill_scripts.entry("grounding-check")
    material = ("| 노선 | 2026년 9월(명) | 2025년 9월(명) |\n"
                "| 정문 | 14,820 | 13,350 |\n| 역 | 21,300 | 17,850 |")
    text = ("- 이용객 합계 36,120명(전년 31,200명 대비 4,920명, 15.8% 증가)\n"
            "- 정문 노선 1,470명 증가")
    assert trace.check(text, material, []) == []
    assert [f["text"] for f in trace.check("- 이용객 합계 38,000명", material, [])] == ["38,000명"]


def test_today_counts_only_when_the_person_said_it():
    trace = skill_scripts.entry("grounding-check")
    text = "회의는 2026년 10월 8일에 열렸습니다."
    assert trace.check(text, "어제 회의 메모야", [], {"today": _TODAY})
    assert not trace.check(text, "오늘 회의 메모야", [], {"today": _TODAY})


def test_order_numbers_citations_and_years_are_not_values():
    trace = skill_scripts.entry("grounding-check")
    text = ("2차 회의에서 3학년 대상 안을 표 1과 같이 정리했다[2]. "
            "2027년부터 REQ-07을 적용한다. 요구사항 6개.")
    assert trace.check(text, "회의 메모", []) == []


def test_the_report_step_says_how_many_were_blanked():
    run = _run("minutes-2__r2")
    sections, outcome = check_skills.ground_sections(
        ("grounding-check",), run["sections"], material=f"{run['request']}\n{run['material']}",
        computed=run["computed"], options=_options(run))
    assert outcome.summary.startswith("근거 없는 값 1개 → 빈칸")
    assert outcome.left == []
    assert any("[시기]" in s["content"] for s in sections)


def test_the_script_runs_as_a_cli(capsys):
    trace = skill_scripts.entry("grounding-check")
    args = {"op": "repair", "text": "기한은 11월 초까지.", "material": "다음 주까지 한다.",
            "computed": []}
    assert trace.main(["trace.py", json.dumps(args, ensure_ascii=False)]) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["text"] == "기한은 [시기]까지."


async def test_a_written_report_is_traced_and_the_step_records_it(monkeypatch):
    from app.services import report

    memo = ("[연구실 주간 미팅 메모 — 2026-10-14(수) 10:00, 참석: 한교수, 오지호, 서유나, 문서아]\n"
            "- GPU 서버: 다음 주까지는 오지호 우선\n- 데이터 정제 → 서유나, 다음 주 수요일까지")
    draft = ("## 결정 사항\n11월 초까지 오지호가 GPU 서버를 우선 사용합니다. "
             "데이터 정제는 서유나가 2026-10-21(수)까지 마칩니다.")

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="연구실 미팅 메모야. 회의록으로 만들어 줘.", model="m", api_key="k",
        web_search=False,
        untrusted_context=[memo], template_id="doc-minutes",
        approved_plan={"title": "주간 미팅 회의록", "sections": ["결정 사항"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    text = "\n".join(s["content"] for s in sections)
    assert "11월 초" not in text and "[시기]까지 오지호가" in text
    assert "2026-10-21(수)" in text
    steps = {e["id"]: e for e in events if e["type"] == "step"}
    assert "근거 없는 값 1개 → 빈칸" in steps["format_checks"]["detail"]


_TOOL_NUMBERS = check_skills.options_for(None, {"tool_numbers": True})


def test_a_rate_in_a_long_request_is_the_person_s_value():
    # Prose commas and 「11,000」 do not make the request a CSV row: the rate it gives is the
    # person's, not a number no tool printed.
    trace = skill_scripts.entry("grounding-check")
    request = ("근로장학생 근무현황 엑셀이에요. 학생별로 9월 근무시간 합계 내 주시고, 시간당 "
               "11,000원 곱해서 지급액도 계산해 주세요. 이상한 행(중복, 학번 오류, 날짜 누락 등)은 "
               "따로 표시해 주세요.")
    rows = "학번\t성명\t근무일자\t근무시간\n32221045\t김민지\t2026-09-03\t4\n"
    computed = ["김민지 12.0"]
    text = "시간당 11,000원으로 계산했습니다. 김민지 12시간, 지급액 132,000원."
    assert trace.check(text, f"{request}\n{rows}", computed, _TOOL_NUMBERS) == []
    # A product with a number no tool printed is still the model's own arithmetic.
    found = trace.check("지급액 140,000원", f"{request}\n{rows}", computed, _TOOL_NUMBERS)
    assert [f["text"] for f in found] == ["140,000원"]


def test_a_data_cell_counts_on_the_line_that_names_its_row():
    # An anomaly list quotes the rows it flags; a mean that equals some cell does not pass.
    trace = skill_scripts.entry("grounding-check")
    rows = ("학번\t성명\t근무일자\t근무시간\n32201533\t이하늘\t2026-09-05\t22\n"
            "32211876\t박서연\t9/1\t3.5\n32211876\t박서연\t2026-09-08\t3\n")
    text = ("| 9 | 이하늘 | 32201533 | 2026-09-05 | 22시간 | 하루 근무가 깁니다 |\n"
            "| 2 | 박서연 | 32211876 | 9/1 | 3.5시간 | 날짜 형식 불일치 |")
    assert trace.check(text, rows, ["n=3"], _TOOL_NUMBERS) == []
    found = trace.check("하루 평균 근무시간은 3.5시간입니다.", rows, ["n=3"], _TOOL_NUMBERS)
    assert [f["text"] for f in found] == ["3.5시간"]


def test_a_year_and_a_count_is_not_a_month():
    trace = skill_scripts.entry("grounding-check")
    material = "연도\tSCIE\tKCI\t학회발표\n2022\t2\t3\t5\n"
    assert trace.check("2022년 5건으로 가장 적었다.", material, ["5"], _TOOL_NUMBERS) == []
    assert [f["kind"] for f in trace.check("2022년 5월에 시작했다.", material, [])] == ["month"]


def test_a_sheet_s_budget_cells_stay_in_the_rate_table():
    # The 집행률 table's 예산·집행 cells and 「2022년 5건」 stay.
    trace = skill_scripts.entry("grounding-check")
    sheet = ("[시트 1: 연구비집행]\n연도\t항목\t예산(천원)\t집행(천원)\n"
             "2024\t인건비\t42000\t40150\n2024\t연구활동비\t18000\t16320\n"
             "2025\t인건비\t45000\t44100\n2025\t연구활동비\t20000\t14200\n\n"
             "[시트 2: 논문실적]\n연도\tSCIE\tKCI\t학회발표\n2022\t2\t3\t5\n2023\t3\t2\t7\n")
    computed = ["2024 인건비 95.60\n2024 연구활동비 90.67\n"
                "2025 인건비 98.00\n2025 연구활동비 71.00"]
    text = ("| 연도 | 항목 | 예산(천원) | 집행(천원) | 집행률(%) |\n|---|---|---|---|---|\n"
            "| 2024 | 인건비 | 42,000 | 40,150 | 95.6 |\n"
            "| 2025 | 연구활동비 | 20,000 | 14,200 | 71.0 |\n\n"
            "SCIE+KCI 합계는 2022년 5건입니다. 연구비집행 시트는 2024·2025년 2개 연도만 있습니다.")
    assert trace.check(text, "집행률을 표로 정리해 줘.\n" + sheet, computed, _TOOL_NUMBERS) == []


def test_a_unit_noted_once_on_the_material_line_scales_its_figures():
    # 「식음료 870 / … 단위 만원」 → 870 under 「집행액(만원)」 stays.
    trace = skill_scripts.entry("grounding-check")
    material = ("아래 자료로 행사 결과보고서 써 줘.\n\n"
                "- 예산: 편성 4,500만원 / 집행 4,215만원 (시상금 2,000 / 식음료 870 / "
                "장소·장비 임차 610 / 멘토·심사 수당 520 / 홍보·기념품 215, 단위 만원)")
    text = ("| 항목 | 집행액(만원) |\n|---|---:|\n| 시상금 | 2,000 |\n| 식음료 | 870 |\n"
            "| 장소·장비 임차 | 610 |\n| 멘토·심사 수당 | 520 |\n| 홍보·기념품 | 215 |\n"
            "식음료는 870만원을 썼다.")
    assert trace.check(text, material, []) == []
    standalone = "(단위: 천원)\n인건비 42000\n연구활동비 18000"
    assert trace.check("| 항목 | 예산(만원) |\n|---|---|\n| 인건비 | 4,200 |", standalone, []) == []


def test_a_blanked_value_takes_its_worked_calculation_with_it():
    # No 「[수치] ÷ 30 ÷ 10개월 = 0만원/대/월」 left after the blank.
    trace = skill_scripts.entry("grounding-check")
    line = "대당 월 비용은 7,200만원 ÷ 30 ÷ 10개월 = 24만원/대/월이다."
    at = line.index("7,200만원")
    found = [{"kind": "number", "text": "7,200만원", "line": 0, "start": at, "end": at + 7,
              "placeholder": "[수치]"}]
    assert trace.fix(line, found) == "대당 월 비용은 [수치]이다."
    # A plain blank with no worked calculation stays a blank in its sentence.
    plain = trace.fix("예산은 7,200만원이다.", [{**found[0], "start": 4, "end": 11}])
    assert plain == "예산은 [수치]이다."


def test_a_deadline_copied_wrong_goes_back_to_the_person_s_day_even_if_a_page_holds_it():
    # 「2026. 11. 6.(금) 18:00까지」 written as 「2026년 11월 18일(수) 00시 2214분까지」 (내선 2214
    # run into the time) goes back, even with a search page holding 11월 18일.
    trace = skill_scripts.entry("grounding-check")
    request = ("각 학과에 보낼 협조 요청 공문을 써 줘.\n\n- 제출 기한: 2026. 11. 6.(금) 18:00까지\n"
               "- 담당: 기획예산팀 윤서영 (내선 2214)")
    page = "2026년 11월 18일(수) 00시 공고 예정"
    text = "제출 기한은 2026년 11월 18일(수) 00시 2214분까지입니다."
    options = {"person": request}
    found = trace.check(text, f"{request}\n{page}", [], options)
    assert [f["kind"] for f in found] == ["date", "time"]
    fixed = trace.fix(text, found)
    assert fixed == "제출 기한은 2026. 11. 6.(금) [시각]까지입니다."
    # A day in another month from the page stays; one not near a deadline becomes the blank.
    elsewhere = f"{request}\n2026년 12월 3일 공고"
    assert trace.check("관련 공고는 2026년 12월 3일에 나왔다.", elsewhere, [], options) == []
    other = trace.check("설명회는 2026년 11월 18일에 연다.", f"{request}\n{page}", [], options)
    assert [f["placeholder"] for f in other] == ["[일자]"]
    # Without the person's text, the page's day passes.
    assert trace.check("설명회는 2026년 11월 18일에 연다.", f"{request}\n{page}", []) == []


def test_a_kpi_block_is_traced_like_the_text():
    # A KPI's 「이듬해 차이 96,000원」 is traced too.
    trace = skill_scripts.entry("grounding-check")
    material = "A안 연 3,840,000원, B안 연 5,712,000원"
    text = "```kpi\n3,840,000원 | A안 연간\n96,000원 | 이듬해 차이\n```"
    found = trace.check(text, material, [])
    assert [f["text"] for f in found] == ["96,000원"]
    assert "[수치] | 이듬해 차이" in trace.fix(text, found)
