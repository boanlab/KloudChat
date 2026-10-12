"""A decision report's verdict follows the code's must-have matrix (verify2 N6, R-03 GPU): the
first-year cost settles an annual-limit must the reading left blank, the opening line's
recommendation is checked too, and a recommendation the repair did not fix is replaced by code."""

from app.services import report_checks, verify

ASK = ("연구실 GPU 연산 자원 도입 안을 비교해서 결정 보고서로 써 줘. "
       "A안: 클라우드 GPU(A100 80GB 4장) 시간당 12,000원, 연간 예상 사용 2,000시간, "
       "초기비용 없음. B안: 온프레미스 서버(A100 80GB 4장) 구매가 1억 2천만원, "
       "연간 전기·유지비 900만원, 내용연수 5년. 필수 조건: 연구비 연간 집행 한도 "
       "3,000만원 이내, 데이터 외부 반출 불가(민감 데이터 포함). "
       "3년 총비용과 필수 조건 충족 여부로 권고해 줘.")
READING = {
    "musts": [{"name": "연구비 연간 집행 한도", "kind": "max", "limit": 30000000, "unit": "원"},
              {"name": "데이터 외부 반출 불가", "kind": "yesno"}],
    "options": [
        {"name": "A안", "musts": {"연구비 연간 집행 한도": None, "데이터 외부 반출 불가": "없음"}},
        {"name": "B안", "musts": {"연구비 연간 집행 한도": None, "데이터 외부 반출 불가": None}},
    ],
}
COSTS = {"options": {
    "A안(클라우드 GPU)": {"total": 72000000, "first_year": 24000000, "next_year": 24000000},
    "B안(온프레미스)": {"total": 147000000, "first_year": 129000000, "next_year": 9000000},
}}


def _musts():
    return report_checks.must_status(READING, report_checks._material_numbers(ASK), ASK, COSTS)


def test_an_annual_limit_is_judged_on_the_computed_yearly_cost():
    status = {o: (r["status"], r["failed"]) for o, r in _musts()["options"].items()}
    assert status["A안"][0] == "탈락" and "데이터 외부 반출 불가 없음" in status["A안"][1]
    assert status["B안"][0] == "탈락"
    assert "129,000,000원 > 조건 30,000,000원" in status["B안"][1][0]
    assert "조건을 모두 충족하는 대안 없음" in report_checks._must_block(_musts())


async def test_the_opening_recommendation_is_checked_and_settled_by_code_when_repair_fails():
    sections = [
        {"heading": "검토 목적과 판단 기준",
         "content": "A안(클라우드 GPU)을 권고합니다(연구비 연간 집행 한도 내 실행 가능). "
                    "이번 검토의 목적은 두 안을 비교하는 것입니다."},
        {"heading": "권고와 조건", "content": "A안은 비용 기준에서 앞섭니다."},
    ]
    pre = report_checks.Prepass(format_id="comparison", musts=_musts())
    found, _ = await verify.find(verify.Doc(sections, ASK), report_checks.checks(pre))
    assert [(f.section, f.code) for f in found] == [(0, "recommend_mismatch")]

    settled, left = report_checks.settle_verdict(sections, found, pre.musts)
    assert left == []
    opening = settled[0]["content"]
    assert opening.startswith(
        "필수 조건을 모두 충족하는 대안이 없습니다(A안: 탈락 — 데이터 외부 반출 불가 없음; "
        "B안: 탈락 — 연구비 연간 집행 한도 129,000,000원 > 조건 30,000,000원).")
    assert "A안(클라우드 GPU)을 권고" not in opening and "두 안을 비교하는 것입니다." in opening


def test_a_verdict_with_one_passing_option_names_it():
    musts = {"musts": ["SSO"], "options": {
        "톡플로우": {"status": "충족", "failed": [], "unknown": []},
        "팀링크": {"status": "탈락", "failed": ["SSO 없음"], "unknown": []}}}
    assert report_checks.verdict_sentence(musts) == (
        "필수 조건을 모두 충족하는 톡플로우를 권고합니다(팀링크: 탈락 — SSO 없음).")
