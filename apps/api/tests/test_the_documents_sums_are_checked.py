"""문서가 적은 식은 검산되고, 검토자는 산수를 판단하지 않는다."""

import pytest

from app.services import arithmetic, lint


def test_a_wrong_sum_is_a_finding_and_a_right_one_is_not() -> None:
    text = (
        "서버 교체 시 3년 총비용은 `2,400만 원 + 1,140만 원 = 3,540만 원`이다. "
        "클라우드는 `62만 원 × 36개월 = 2,232만 원`, 연간 62만 원 × 12개월 = 744만 원. "
        "2,400만 원 ÷ 744만 원 ≈ 3.2년. 이득 1.405 ÷ 2.00 = 0.703."
    )
    assert arithmetic.findings(text) == []
    wrong = arithmetic.findings("합계는 960만 원 + 240만 원 = 1,300만 원이다.", where="비용")
    assert len(wrong) == 1
    assert wrong[0]["where"] == "비용" and "1,200만 원" in wrong[0]["message"]


def test_the_linter_files_a_wrong_sum_as_p0() -> None:
    parts = lint.from_sections(
        [
            {
                "id": "a",
                "heading": "비용",
                "content": "첫해 비용은 960만 원 + 240만 원 = 1,300만 원입니다.",
            }
        ]
    )
    found = [f for f in lint.check(parts) if f.rule == "arithmetic"]
    assert len(found) == 1 and found[0].severity == "P0" and found[0].where == "비용"


@pytest.mark.asyncio
async def test_a_reviewers_arithmetic_complaint_is_dropped_when_the_sums_hold(monkeypatch) -> None:
    from app.services import critique

    async def complete(_model, _prompt, _key):
        return (
            '{"score": 6.0, "findings": ['
            '{"severity": "P1", "where": "대안 비교", '
            '"message": "3,540만 원과 2,232만 원의 차액 계산이 일치하지 않는다."},'
            '{"severity": "P1", "where": "요약", "message": "결론에 담당과 기한이 없다."}]}'
        ), {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(critique, "_complete", complete)

    result, _ = await critique.review(
        title="서버",
        body="서버 교체 3,540만 원, 클라우드 2,232만 원. 차이 3,540만 원 − 2,232만 원 = 1,308만 원",
        rubric="",
        model="m",
        api_key="k",
    )
    assert [f["where"] for f in result["findings"]] == ["요약"]
