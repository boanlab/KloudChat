"""A request about material pasted under it is not a question about now: the dates, prices
and versions in that material are what it says (the current-fact guard used to fire on them
and add 「학습 시점의 지식」 to a release note written from a commit log)."""

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
