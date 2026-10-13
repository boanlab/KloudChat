"""r7 A02-T2: a pasted ministry notice's 「재정 진단 대상」 made a 보도자료 summary a medical
turn — a forced search, medical grounding and a 「실제 교육부 발표와 대조하지 않았습니다」 line.
The instruction names the turn's domain; pasted material counts only with two different words
of one domain."""

from __future__ import annotations

from app.services import exact_facts

_A02_T2 = (
    "아래 발표문을 부서장 보고용으로 정리해 줘. 핵심은 5줄 이내로 요약하고, 우리 대학(비수도권 "
    "사립대, 재학생 약 8,000명, 올해 RISE 사업 참여 중)에 미치는 영향 3가지와 우리가 지금 준비할 "
    "일을 정리해 줘.\n\n---\n[교육부 보도 참고자료(가상)] 2027년 대학 재정지원사업 개편 방향\n\n"
    "교육부는 2027년부터 대학 재정지원사업을 '성과 중심·지역 연계형'으로 개편한다고 밝혔다. 이번 "
    "개편은 2025년 도입된 지역혁신중심 대학지원체계(RISE)의 안착과 학령인구 감소에 대응한 대학 "
    "구조 혁신을 동시에 추진하기 위한 것이다.\n\n"
    "넷째, 재정 위험 대학에 대한 관리를 강화한다. 운영손익 3년 연속 적자이면서 신입생 충원율 80% "
    "미만인 대학은 '재정 진단 대상'으로 지정되며, 지정 대학은 경영 개선 계획을 제출하고 컨설팅을 "
    "받아야 한다. 진단 대상 대학은 일반재정지원 성과 배분에서 제외된다."
)


def test_a_notice_to_summarise_is_no_medical_turn_and_no_lookup():
    assert exact_facts.advice_domain(_A02_T2) is None
    assert exact_facts.search_reason(_A02_T2) is None


def test_a_budget_or_firm_diagnosis_is_not_a_body_s():
    assert exact_facts.advice_domain("우리 회사 경영 진단 결과를 요약해 줘") is None
    assert exact_facts.advice_domain("재정진단 지표가 뭐야?") is None
    assert exact_facts.advice_domain("두통이 심한데 진단을 받아야 할까?") == "medical"


def test_medical_material_still_counts():
    assert exact_facts.search_reason(
        "이 결과 어때?\n\n---\n혈압 150/95, 공복 혈당 130, 두통이 잦음") == "medical"
    assert exact_facts.advice_domain("건강검진 결과 봐 줘\n\n---\nLDL 170") == "medical"
