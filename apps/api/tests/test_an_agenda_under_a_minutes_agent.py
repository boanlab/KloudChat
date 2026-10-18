"""Agenda requests under a minutes agent written as agendas."""

from __future__ import annotations

from app.pipeline import report_stages
from app.services import doc_formats, process_settings

#: An agenda request sent through the 회의록 비서 agent (template doc-minutes).
_AGENDA_REQUEST = (
    "다음 주 회의 안건 초안을 같이 잡아 줘.\n\n다음 주 목요일(10/15) 14시 교육혁신팀 주간회의야. "
    "1시간이고, 다룰 것: LMS 1차 시안 검토(업체 담당자 30분 참석 예정), 우수 강의 사례집 디자인 "
    "업체 결정(A사 380만원 vs B사 420만원·납기 1주 빠름), 11월 예산 집행계획 취합 현황, "
    "워크숍(10/22) 준비 점검."
)


def test_the_request_names_the_agenda_over_the_minutes_template():
    found = doc_formats.detect(_AGENDA_REQUEST, "doc-minutes")
    assert found is not None and found.id == "agenda"
    assert doc_formats.detect(_AGENDA_REQUEST, None, "minutes").id == "agenda"


def test_minutes_material_that_mentions_its_agenda_stays_minutes():
    for request in (
        "어제 회의 메모야. 정식 회의록으로 만들어 줘. 안건 초안대로 진행했어.",
        "회의 안건 3개를 논의했어. 메모로 정리해 줘.",
        "회의 메모로 기록을 남겨 줘. 안건 지난주 것 그대로.",
    ):
        assert doc_formats.detect(request, "doc-minutes").id == "minutes", request
    # an agenda template asked for minutes is minutes
    assert doc_formats.detect("어제 회의 녹취로 회의록 만들어 줘.", "doc-agenda").id == "minutes"


def test_the_agenda_gets_its_own_settings_and_no_minutes_repair():
    bundle = report_stages.bundle_for(
        request=_AGENDA_REQUEST, model="m", api_key="k", template_id="doc-minutes",
        process={"stages": ["minutes-repair", "minutes-reground"], "checks": ["minutes-rows"]})
    assert bundle.spec.format_id == "agenda"
    assert bundle.request.template_id == "doc-agenda"
    assert not bundle.spec.settings.has_stage("minutes-repair")
    assert not bundle.spec.settings.has_stage("minutes-reground")
    assert "minutes-rows" not in bundle.spec.settings.checks
    note = bundle.request.trusted[-1]
    assert "회의 안건, not a 회의록" in note and "시간 배분(안)" in note


def test_minutes_keep_their_repair():
    bundle = report_stages.bundle_for(
        request="어제 회의 메모야. 정식 회의록으로 만들어 줘.", model="m", api_key="k",
        template_id="doc-minutes")
    assert bundle.spec.format_id == "minutes"
    assert bundle.request.template_id == "doc-minutes"
    assert bundle.spec.settings.has_stage("minutes-repair")
    assert "minutes-rows" in bundle.spec.settings.checks
    assert not any("작성 메모" in t for t in bundle.request.trusted)


def test_an_everyone_owner_is_not_blanked():
    # 「전원」 not blanked to 「[담당자]」 when the memo never says 「전원」
    from app.services import report

    table = ("| 할 일 | 담당 | 기한 |\n|---|---|---|\n"
             "| 담당 사업 11월 예산 집행계획 작성 → 팀장 제출 | 전원 | 10/23(금) |\n"
             "| 사례집 원고 취합 | 박지훈 | 10/23(금) |")
    memo = "10/23까지 각자 담당 사업 11월 예산 집행계획을 팀장에게 제출. 사례집 원고는 박지훈 담당."
    out = report._unsourced_owner_dates(table, memo)
    assert "| 전원 |" in out and "[담당자]" not in out


def test_format_only_steps_leave_other_formats():
    full = process_settings.Process(stages=("minutes-repair", "page-limit"),
                                    checks=("lead-dates", "minutes-rows"))
    trimmed = process_settings.only_for_format(full, "agenda")
    assert trimmed.stages == ("page-limit",) and trimmed.checks == ("lead-dates",)
    assert process_settings.only_for_format(full, "minutes") == full
