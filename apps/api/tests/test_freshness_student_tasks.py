"""Student task instructions are not requests to retrieve changing facts."""

import pytest

from app.services.freshness import current_fact_required

STUDENT_TASKS = [
    "회귀와 분류를 구별하는 연습 중이야. 내일 기온 예측, 이메일 스팸 여부 판단, "
    "집값 예측을 각각 회귀/분류로 표시하고 이유를 한 구절씩 붙여줘.",
    "교환학생 팀원에게 보낼 문장이야. 자연스러운 영어로 번역만 해줘. "
    "'오늘 회의에서 정하지 못한 일정은 내일 오전까지 단체 채팅방에서 확정하겠습니다.'",
    "팀원에게 자료 업로드를 부탁하는 메시지를 써줘. 마감은 오늘 20시이고 이유는 "
    "내일 발표 리허설 준비야. 비난하는 말 없이 두 문장으로 부탁해.",
    "A는 혼자 6시간, B는 혼자 3시간이면 같은 작업 하나를 끝내. 둘이 일정한 속도로 "
    "처음부터 함께 일하면 몇 시간 걸려? 작업률 식도 한 줄 보여줘.",
    "이번 주 할 일 우선순위를 정하는 걸 도와줘. 내일 제출할 보고서 1시간, 사흘 뒤 시험 "
    "복습 3시간, 다음 주 동아리 포스터 2시간이야. 오늘 2시간을 어떻게 쓸지 이유와 함께 제안해줘.",
    "첨부한 가상 강의계획서만 보고 평가 항목과 비중을 표로 정리해줘. "
    "성적 이의신청 기간도 자료에 있으면 적고, 없으면 없다고 말해줘.",
    "첨부한 공지 v1과 v2의 달라진 점만 비교해줘. "
    "최종 제출 기준은 어느 버전을 따라야 하는지도 자료 근거로 알려줘.",
    "실시간 조회가 아니라 제공값으로만 계산해줘. 가상 환율표의 기준일은 2026-09-01이고 "
    "1달러=1,300원이야. 20달러는 이 표 기준 몇 원인지 계산하고, "
    "지금 환율을 확인한 것은 아니라는 점을 짧게 밝혀줘.",
]


@pytest.mark.parametrize("question", STUDENT_TASKS)
def test_classification_translation_drafting_and_constant_rates_are_not_current_lookups(question):
    assert not current_fact_required(question)


@pytest.mark.parametrize("question", [
    "내일 서울의 기온을 알려줘.",
    "오늘 과제 제출 마감은 몇 시야?",
    "한국의 현재 기준금리는 얼마야?",
    "대통령의 현재 일정은 무엇이야?",
    "두 버전의 출시일을 비교하고 최신 버전도 확인해줘.",
    "팀원에게 공지를 쓰려고 해. 내일 실제 날씨를 확인해줘.",
    "회귀 과제를 하고 있어. 오늘 실제 환율은 얼마야?",
    "이 문장을 영어로 번역해줘. 그리고 현재 대한민국 대통령은 누구야?",
    "첨부한 공지의 버전을 비교하고 현재 Python 최신 버전도 확인해줘.",
    "첨부한 자료 기준 금리가 지금 실제 기준금리와 같은지 검증해줘.",
    "자료에 없으면 검색해서 현재 장학금 신청 기간을 찾아줘.",
    "가상 환율표로 계산해줘. 그리고 현재 실제 원/달러 환율도 알려줘.",
    "이 표 기준 20달러가 몇 원인지 계산하고 지금 환율이 얼마인지 확인해줘.",
    "오늘 K리그 순위를 알려줘.",
    "첨부한 지난해 날씨표를 참고해서 내일 서울 기온을 알려줘.",
    "첨부한 작년 출시표를 참고해서 올해 출시 일정을 알려줘.",
    "첨부한 가격표 기준으로 오늘 환율을 반영한 달러 가격을 알려줘.",
    "지금 환율을 알려주되 확인한 것은 아니라는 점을 밝혀줘.",
    "지금 대통령이 누구인지 말하고 확인한 것은 아니라는 점을 밝혀줘.",
    "현재 국가장학금 지원 우선순위를 알려줘.",
    "현재 대학 기숙사 배정 우선순위를 알려줘.",
    "첨부한 작년 출시표를 무시하고 올해 출시 일정을 정리해줘.",
    "첨부한 지난해 날씨표를 참고해서 내일 서울 기온을 예측해줘.",
])
def test_student_context_does_not_exempt_a_separate_current_fact_request(question):
    assert current_fact_required(question)


@pytest.mark.parametrize("question", [
    '아래 질문에 답한 뒤 영어로 번역해줘. "현재 대한민국 대통령"은 누구야?',
    '한국어로 요약해줘. "현재 한국 기준금리"가 몇 퍼센트인지 먼저 확인해.',
    '영어로 번역해줘. "현재 원/달러 환율"을 찾아서 알려줘.',
    "내일 기온 예측을 회귀/분류로 표시하고 기온을 직접 예측해줘.",
    "Classify tomorrow temperature prediction as regression or classification, "
    "then estimate Seoul temperature tomorrow.",
])
def test_a_quoted_lookup_target_and_an_actual_prediction_keep_verification(question):
    assert current_fact_required(question)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", STUDENT_TASKS)
async def test_student_task_router_does_not_attach_a_current_fact_hold(monkeypatch, question):
    from test_plain_chat_tools import _routed_turn

    captured = await _routed_turn(monkeypatch, strict=False, question=question)
    assert captured["freshness_request"] is None
