"""Deck purposes (13): read from the request's own words, the narrower one taken when two match,
each with a usual flow whose layouts the planner knows, questions the gap check can run, and
checks; the persona decks pick theirs."""

from __future__ import annotations

import json

import pytest

from app.services import deck, gap_questions, patterns


@pytest.mark.parametrize(("request_text", "purpose"), [
    # 실적·성과 보고
    ("매출 실적 엑셀로 경영진 보고용 분기 실적 PowerPoint 8장 만들어 줘.", "results"),
    ("3분기 성과 보고 발표 자료 만들어 줘", "results"),
    ("KPI 목표 대비 달성률 슬라이드", "results"),
    ("Make a quarterly results deck for the board", "results"),
    # 연구·논문 발표
    ("랩 세미나에서 vLLM 논문 발표할 PowerPoint 12장 만들어 줘.", "research"),
    ("저널 클럽에서 소개할 논문 리뷰 슬라이드", "research"),
    ("학회 발표 자료 15분 분량", "research"),
    ("slides for a paper review at our journal club", "research"),
    # 심사 발표
    ("석사 학위 논문 심사 발표 자료", "defense"),
    ("박사 예비심사 디펜스 슬라이드", "defense"),
    # 수업 과제 발표
    ("경영학원론 조별 과제 발표 자료 10분", "student"),
    ("기말 발표 PPT 만들어 줘, 팀플이야", "student"),
    # 투자 IR
    ("시리즈 A 투자 유치용 IR 자료", "ir"),
    ("투자자 미팅에 쓸 피치덱", "ir"),
    ("a seed round pitch deck", "ir"),
    # 프로젝트 결과 보고
    ("ERP 구축 프로젝트 결과 보고 발표", "retrospective"),
    ("지난 분기 장애 사후 검토와 교훈", "retrospective"),
    ("project retrospective slides", "retrospective"),
    # 제품·서비스 소개
    ("고객사 미팅용 우리 솔루션 소개 자료", "product"),
    ("신제품 서비스 소개 영업 자료", "product"),
    # 제안 발표
    ("정부 지원사업 공모 발표 자료", "proposal"),
    ("예산 승인 요청 제안 발표", "proposal"),
    # 정책·제도 안내
    ("유연근무 제도 변경 안내 설명회 자료", "policy"),
    ("내년부터 달라지는 점 시행 안내 슬라이드", "policy"),
    # 교육·온보딩
    ("신입 사원 온보딩 교육 자료", "training"),
    ("보안 교육 자료 만들어 줘", "training"),
    ("an onboarding training for new engineers", "training"),
    # 강의
    ("운영체제 7주차 강의 자료", "lecture"),
    ("고등학교 수업 자료로 광합성 슬라이드", "lecture"),
    # 케이스 발표
    ("클라우드 이전 대안 비교와 권고안 발표", "case"),
    # 회의·브리핑
    ("다음 주 운영 회의 안건 자료", "meeting"),
    ("주간 회의에서 진행 현황 공유할 슬라이드", "meeting"),
    ("a kickoff meeting deck", "meeting"),
    # 아무 용도도 말하지 않음
    ("고양이 사진 모음 슬라이드", None),
    ("우리 동네 맛집 소개 슬라이드", None),
    # English words inside other words are no match
    ("trainingset 통계 표", None),
])
def test_a_deck_purpose_is_read_from_the_request(request_text, purpose):
    found = patterns.deck_purpose(request_text)
    assert (found.id if found else None) == purpose


def test_two_purposes_take_the_narrower_and_both_are_said():
    request = "분기 실적을 주간 회의에서 공유할 자료"
    found = patterns.deck_purposes(request)
    assert [p.id for p in found[:2]] == ["results", "meeting"]
    step = patterns.step(request, "slides")
    assert step["label"] == "구조: 실적·성과 보고 패턴"
    assert "회의·브리핑 자료" in step["detail"]
    assert found[0].wire(found[1:])["also"] == ["회의·브리핑 자료"]


def test_pasted_material_does_not_pick_the_purpose():
    request = "이 메모로 발표 자료 만들어 줘\n\n---\n강의 계획서: 1주차 소개, 2주차 실습"
    assert patterns.deck_purpose(request) is None


def test_there_are_thirteen_purposes_each_complete():
    known = set(deck._LAYOUTS)
    found = patterns.deck_patterns()
    assert len(found) == 13
    assert len({p.order for p in found}) == len(found)
    for purpose in found:
        assert purpose.name and purpose.name_en and purpose.rules.strip()
        assert len(purpose.checks) >= 3
        assert len(purpose.flow) >= 4
        for part, slides, layouts in purpose.flow:
            assert part and slides and layouts
            assert set(layouts) <= known, (purpose.id, set(layouts) - known)
        # the gap check can store and run each question
        assert gap_questions.clean(list(purpose.questions)) == [
            {k: q[k] for k in ("id", "label", "question", "why", "placeholder", "detector")}
            for q in purpose.questions]


def test_a_results_deck_asks_for_the_period_and_targets_only_when_missing():
    results = next(p for p in patterns.deck_patterns() if p.id == "results")
    from app.services import skill_scripts

    gaps = skill_scripts.module("gap-check", "gaps")
    asked = gaps.check("분기 실적 발표 자료 만들어 줘", "제품별 매출 표",
                       options={"questions": list(results.questions)})
    assert {f["fact"] for f in asked} == {"period", "targets"}
    asked = gaps.check("2026년 3분기 실적 발표 자료", "목표 대비 실적 표",
                       options={"questions": list(results.questions)})
    assert asked == []


@pytest.mark.parametrize("purpose", [p.id for p in patterns.deck_patterns()])
async def test_each_purposes_flow_is_a_plan_the_deck_planner_keeps(monkeypatch, purpose):
    found = next(p for p in patterns.deck_patterns() if p.id == purpose)
    slides = [{"title": found.name, "layout": "title"}] + [
        {"title": part[:20], "layout": layouts[0]} for part, _, layouts in found.flow]
    seen: list = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        seen.append(messages)
        return json.dumps({"title": found.name, "slides": slides}, ensure_ascii=False), {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(deck, "_complete", complete)
    events = [e async for e in deck.write(
        request=f"{found.name} 자료 {len(slides)}장", model="m", api_key="k",
        web_search=False, may_ask=False, trusted_context=[found.note()])]
    plan = next(e["plan"] for e in events if e["type"] == "proposal")
    assert len(plan["slides"]) == len(slides)
    assert all(s["layout"] in deck._LAYOUTS for s in plan["slides"])
    # the planner is told the pattern and its flow
    assert f"[패턴: {found.name}]" in json.dumps(seen[0], ensure_ascii=False)
