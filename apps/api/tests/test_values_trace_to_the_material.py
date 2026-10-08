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
    return check_skills.options_for(run["facts"]) | {"today": _TODAY}


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
