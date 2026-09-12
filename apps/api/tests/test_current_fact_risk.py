"""Mutable-fact selection never claims to determine whether an answer is true."""

import unicodedata
from datetime import date

import pytest

from app.services.freshness import current_fact_required, fresh_fact_required

TODAY = date(2026, 9, 12)


@pytest.mark.parametrize("question", [
    "현재 대한민국 대통령",
    "대한민국 대통령은 누구야?",
    "지금 한국 국무총리 이름을 알려줘",
    "Who is the current president of Korea?",
    "Who is the CEO of Example Corp?",
    "현재 Acme의 CEO는 누구야?",
    "Example Corp의 현재 대표이사는?",
    "지금 단국대학교 총장은 누구야?",
    "현재 국가대표 감독이 누구야?",
    "최신 Python 버전은?",
    "What is the latest stable Python version?",
    "이 제품의 출시일 알려줘",
    "다음 업데이트 일정은?",
    "현재 달러 환율 얼마야?",
    "환율 알려줘",
    "오늘의 기준금리는?",
    "What is the current interest rate?",
    "현재 비트코인 가격은?",
    "이 제품 가격을 비교해줘",
    "What is the price of this product?",
    "오늘 서울 날씨는?",
    "Weather in Seoul",
    "내일 부산 기온 알려줘",
    "이번 주 경기 일정 알려줘",
    "현재 리그 순위표는?",
    "Who is the latest tournament winner?",
    "이번 대회 우승팀은?",
    "현재 이 버전 지원 종료 여부 알려줘",
    "이번 달 접수 마감은 언제야?",
    "내년 신청 기간은?",
    "What are the upcoming deadlines?",
    "현재 일본 입국 규정은?",
    "Current visa requirements for Japan",
    "이 카페 영업 시간은?",
    "현재 대한민국 인구는?",
    "최신 실업률 통계 알려줘",
    "최근 기업 실적을 알려줘",
    "현재 서비스 상태는?",
    "올해 시행 중인 법령을 확인해줘",
    "최신 제품 사양 알려줘",
    "요즘 선거 결과 알려줘",
    "2026년 현재 대한민국 대통령",
    "2026년 대한민국 인구 통계는?",
    "2027년 대회 일정 알려줘",
    "검색하지 말고 현재 대한민국 대통령을 알려줘",
    "Do not search the web. Who is the current CEO?",
    'Translate "현재 대한민국 대통령" into English. Also who is the current CEO?',
    '"현재 환율"을 영어로 번역해줘. 그리고 실제 현재 환율은 얼마야?',
    "환율의 개념을 설명해줘. 그리고 현재 달러 환율도 알려줘",
    "2020년 당시 대표는 누구였어? 그리고 지금 대표는 누구야?",
    "가상 국가의 대통령을 만들어줘. 실제 현재 대한민국 대통령도 알려줘",
    "대통령 이야기는 그만하고 현재 환율 알려줘",
    "현재 대통령 이름만 알려줘. 역할은 설명하지 마",
    "현재 대통령이 누구인지 알려주고 역할은 설명하지 마",
    "현재 CEO가 누구인지 알려주고 역할은 설명하지 마",
    "최신 환율과 환율의 정의 알려줘",
    "현재 CEO와 CEO의 역할을 알려줘",
    "최신 가격과 그 정의를 알려줘",
    "가격은 100원이라고 제공했어. 제공 가격과 실제 현재 가격을 비교해줘",
    "현재 상태는 정상이라고 제공했어. 제공 정보 말고 지금 현재 상태는?",
    "가격은 100원이라고 제공했어. 제공된 가격만 알려주고 실제 현재 환율도 알려줘",
    "날씨 인사말 써줘. 그리고 내일 실제 서울 날씨도 알려줘",
    "현재 날씨 인사 문구를 작성해줘. 그리고 현재 환율을 알려줘",
])
def test_mutable_fact_requires_current_evidence_across_domains(question):
    assert current_fact_required(question, as_of=TODAY)


@pytest.mark.parametrize("question", [
    "", "안녕", "한국어로 인사해줘", "오늘도 안녕하세요", "지금 너무 피곤해",
    "Hello!", "How are you today?", "I'm sad today. Please comfort me.",
    "1+1은?", "0+0은?", "지금 1+1은 얼마야?", "현재 0+0은?",
    "Calculate 1+1 now", "What is two plus two?",
    "전자와 양성자의 차이를 설명해줘", "What is photosynthesis?",
    "환율의 개념을 설명해줘", "금리란 무엇인가요?", "환율이란?",
    "주가 계산 방법 알려줘", "날씨 예보의 원리를 설명해줘",
    "What is an exchange rate?", "Explain the definition of inflation",
    "How does weather forecasting work?", "대통령의 권한은?",
    "현재 대통령의 역할을 설명해줘", "Who can become president?",
    "현재 CEO의 역할을 설명해줘", "최신 환율의 정의 알려줘",
    "현재 대통령의 역할과 권한을 알려줘",
    "2010년 대한민국 대통령은 누구였어?", "2020년 현재 대한민국 대통령은?",
    "Who was the president in 2010?", "What was the exchange rate in 2020?",
    "2020년 당시 달러 환율 알려줘", "2020년 현재 환율은?",
    "2021년 대회 우승팀 알려줘", "과거의 금리 변화 설명해줘",
    "전직 대표이사는 누구였어?",
    'Translate "현재 대한민국 대통령" into English',
    'Translate into English: "오늘 환율은 얼마인가요?"',
    '"현재 가격"을 영어로 번역해줘',
    "다음 문장을 번역해줘: 현재 대통령의 이름은 비공개입니다",
    "Summarize this text: current prices have changed",
    "다음 자료만 요약해줘: 현재 제품 가격은 정해지지 않았습니다",
    "가상 국가의 현직 대통령을 만들어줘", "현재 환율을 소재로 시를 써줘",
    "Write a fictional story about the current president",
    "날씨 좋은 날을 배경으로 동화를 써줘",
    "현재 대통령을 알려주지 마", "Do not tell me the current CEO",
    "현재 대통령 이야기는 그만하고 1+1을 계산해줘",
    "지금 날씨 얘기는 그만하고 한국어로 인사해줘",
    "가격은 100원이라고 제공했어. 제공된 가격만 알려줘.",
    "현재 상태는 정상이라고 제공했어. 그 상태만 알려줘.",
    "제공된 가격만 알려줘",
    "주어진 현재 상태만 그대로 말해줘",
    "날씨 인사말 써줘",
    "현재 날씨 인사 문구를 작성해줘",
    "Write a weather greeting",
])
def test_stable_or_nonfactual_requests_do_not_get_a_freshness_gate(question):
    assert not current_fact_required(question, as_of=TODAY)


def test_hangul_normalization_does_not_bypass_selection():
    request = unicodedata.normalize("NFD", "현재 대한민국 대통령")
    assert current_fact_required(request, as_of=TODAY)


def test_cutoff_year_boundary_is_explicit_and_not_a_model_name_guess():
    assert current_fact_required("2026년 현재 환율은?", as_of=date(2026, 9, 12))
    assert not current_fact_required("2026년 현재 환율은?", as_of=date(2027, 1, 1))


@pytest.mark.parametrize("question", [None, 123, {}, []])
def test_non_text_request_is_not_a_factual_claim(question):
    assert not current_fact_required(question, as_of=TODAY)


def test_legacy_detector_remains_available_and_politically_scoped():
    assert fresh_fact_required("현재 대한민국 대통령", as_of=TODAY)
    assert not fresh_fact_required("현재 달러 환율", as_of=TODAY)
    assert current_fact_required("현재 달러 환율", as_of=TODAY)
