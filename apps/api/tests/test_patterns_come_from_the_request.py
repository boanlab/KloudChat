"""Structural patterns are chosen from the request, not from a gallery: a report's purpose
format brings its questions and checks with no template picked, and a deck's purpose brings
its rules to the planner."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession, SessionKind
from app.routers import sessions as sessions_router
from app.services import design_templates, gap_questions, patterns


@pytest.mark.parametrize(("request_text", "purpose"), [
    ("다음 주 운영 회의에 쓸 주간 브리핑 자료 5장 만들어 줘", "briefing"),
    ("학위 논문 심사 발표 자료를 만들어 줘", "defense"),
    ("신입 사원 교육 자료로 Git 강의 슬라이드 만들어 줘", "lecture"),
    ("투자 유치용 피치 덱 10장", "proposal"),
    ("두 대안 비교해서 경영진 보고 케이스 발표 자료", "case"),
    ("고양이 사진 모음 슬라이드", None),
])
def test_a_deck_purpose_is_read_from_the_request(request_text, purpose):
    found = patterns.deck_purpose(request_text)
    assert (found.id if found else None) == purpose


def test_pasted_material_does_not_pick_the_purpose():
    request = "이 메모로 발표 자료 만들어 줘\n\n---\n강의 계획서: 1주차 소개, 2주차 실습"
    assert patterns.deck_purpose(request) is None


def test_a_deck_pattern_tells_the_planner_its_rules():
    note = patterns.deck_purpose("주간 브리핑 자료 만들어 줘").note()
    assert note.startswith("[패턴: 사내 브리핑]") and "owners and deadlines" in note
    assert all(p.checks for p in patterns.deck_patterns())


def test_a_report_pattern_is_the_format_the_request_names():
    assert patterns.report_format("어제 회의 내용으로 회의록 정리해 줘") == "minutes"
    # a picked template still fixes its own format
    assert patterns.report_format("보고서 써 줘", "doc-minutes") == "minutes"
    assert patterns.report_format("그냥 정리해 줘") is None


def test_a_report_pattern_brings_its_checks_and_questions_without_a_template():
    minutes = design_templates.for_format("minutes")
    session = ChatSession(user_id="u", kind=SessionKind.report)
    assert sessions_router.render_template_checks(session, "minutes") == minutes.check_skills
    assert sessions_router.render_template_checks(session, None) == ()
    asked = gap_questions.resolve(own=None, template_id=None, doc_format="minutes")
    assert {q["id"] for q in asked} >= {"meeting_date", "attendees"}
    assert patterns.checklist("minutes") == tuple(minutes.checks)
