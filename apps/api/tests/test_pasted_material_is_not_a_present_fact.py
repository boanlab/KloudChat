"""A request about material pasted under it is not a question about now: the dates, prices
and versions in that material are what it says, so the current-fact guard adds no
「학습 시점의 지식」 to a release note written from a commit log."""

from app.routers.sessions import _reads_pasted_material
from app.services import freshness

LOG = ("아래 커밋 로그로 고객용 릴리스 노트 써 줘. 버전은 2.8.0, 배포일 2026-10-20.\n\n"
       "a1f3c2e feat: 주문 내역 CSV 내보내기 추가 (#812)\n"
       "b7d9e01 fix: 쿠폰 중복 적용 시 결제 금액 오류 수정 (#815)\n"
       "c3e4f56 perf: 상품 목록 첫 화면 로딩 개선 (#820)\n"
       "d8a1b23 chore: CI 캐시 설정 정리\n")
PACK = ("투자 검토용으로 (주)모아헬스케어 기업 개요 정리해 줘.\n\n"
        "[자료 1] DART, 「(주)모아헬스케어 사업보고서(2025)」, 2026-03-28 공시 — "
        "연결 매출 312억원, 연결 영업손실 27억원\n"
        "[자료 2] 회사 IR 자료(2026-08): 2026 상반기 연결 매출 171억원")


def test_a_request_over_pasted_material_reads_the_material():
    assert freshness.current_fact_required(LOG)  # what the guard sees on its own
    assert _reads_pasted_material(LOG)
    assert _reads_pasted_material(PACK)


def test_the_request_itself_can_still_ask_about_now():
    asks_now = PACK.replace("기업 개요 정리해 줘.", "지금 주가랑 기업 개요 정리해 줘.")
    assert not _reads_pasted_material(asks_now)
    # No material under it: a question, not a paste.
    assert not _reads_pasted_material("삼성전자 대표가 누구야?")
    assert not _reads_pasted_material("삼성전자 대표가 누구야?\n\n그리고 애플 대표는?")


def test_a_present_word_inside_the_task_is_not_a_question_about_now():
    # 「지금 준비할 일」, 「이번 주」, 「예정」 in a work request over the person's own memo
    # are not a present fact and draw no 「학습 시점의 지식 … 웹 검색을 켜서 확인하세요」.
    policy = ("아래 발표문을 부서장 보고용으로 정리해 줘. 우리 대학(올해 RISE 사업 참여 중)에 "
              "미치는 영향 3가지와 우리가 지금 준비할 일을 정리해 줘.\n\n---\n"
              + "교육부는 2027년부터 대학 재정지원사업을 개편한다. " * 12)
    weekly = ("아래 메모로 이번 주 주간 업무 보고서 써 줘. 팀장님께 올릴 거야.\n\n"
              "- 기간: 10/5~10/9\n- LMS 개편: 업체 1차 시안 일정 확인(10/15 도착 예정)\n"
              "- AI 교수법 워크숍(10/22): 신청 42명 마감, 대기 2명 온라인 송출로 수용 결정\n"
              "- 이슈: 디자인 업체 견적 A사 380만원 / B사 420만원(납기 1주 빠름) — 결정 필요")
    agenda = ("다음 주 회의 안건 초안을 같이 잡아 줘.\n\n"
              "다음 주 목요일(10/15) 14시 교육혁신팀 주간회의야. 1시간이고, 다룰 것: LMS 1차 시안 "
              "검토(업체 담당자 30분 참석 예정), 디자인 업체 결정(A사 380만원 vs B사 420만원), "
              "11월 예산 집행계획 취합 현황, 워크숍(10/22) 준비 점검.")
    assert all(_reads_pasted_material(text) for text in (policy, weekly, agenda))
    # A present-state question over the same material still is one.
    assert not _reads_pasted_material(policy.replace("정리해 줘.", "지금 기준금리는 얼마야?", 1))
