"""A supplied-question review can explain ambiguity without grading a learner."""

import json

import pytest
from test_ncs_preflight import _call, _stream, _text

from app.services import agent
from app.services.tools.base import ToolContext
from app.services.tools.ncs_check import CHECK_NCS_ANSWER, check_ncs_answer

REVIEW = (
    "NCS 문항을 검토해줘. '20% 증가한 뒤 20% 감소한 값은 원래 값과 비교하면?' "
    "선택지: 1번 4% 감소, 2번 원래의 96%, 3번 변화 없음, 4번 4% 증가. "
    "정답이 하나로 정해지는지도 설명해줘."
)
ARGUMENTS = {
    "decision": "calculate",
    "expression": "(1 + 0.2) * (1 - 0.2)",
    "choices": ["0.96", "0.96", "1", "1.04"],
}


def _context(prompt):
    return ToolContext(user_id="learner", session_id="review", request=prompt)


async def test_supplied_equivalent_choices_keep_value_and_matching_indices():
    output = await check_ncs_answer(ARGUMENTS, _context(REVIEW))
    data = json.loads(output.content)
    assert data["exact"] == "24/25" and data["value"] == "0.96"
    assert data["matched_choices"] == [1, 2] and data["answer"] is None
    assert data["grading"] == "not_requested"
    assert "0.96" in output.final_text
    assert "1번, 2번" in output.final_text
    assert "유일한 정답" in output.final_text and "채점은 보류" in output.final_text
    assert "실제 문항" in output.final_text and "별도로 확인" in output.final_text
    assert "0.96" not in output.detail


async def test_no_match_review_distinguishes_a_missing_matching_choice():
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "1600/20", "choices": ["79", "82.5"]},
        _context("다음 문항을 검토해줘. 선택지: 1번 79, 2번 82.5. 정답이 유일한가요?"),
    )
    assert "80" in output.final_text
    assert "일치하는 것이 없어" in output.final_text
    assert "채점은 보류" in output.final_text
    assert json.loads(output.content)["answer"] is None


async def test_review_explains_when_duplicate_matches_use_requested_rounding():
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "1/3", "choices": ["0.33", "33/100"],
         "decimal_places": 2},
        _context("선지 검토해줘. 선택지: 1번 0.33, 2번 33/100. 소수점 2자리로 반올림."),
    )
    assert "1/3" in output.final_text
    assert "소수점 2자리 반올림값은 0.33" in output.final_text
    assert "1번, 2번" in output.final_text


@pytest.mark.parametrize(
    "prompt",
    [
        None,
        "문항을 만들어줘. 정답은 아직 알려주지 마.",
        "선택지 1번, 2번이 있는 새 문항을 만들어줘. 정답이 유일한지도 검토해줘.",
        "선지 1번, 2번인 퀴즈를 작성해줘. 정답이 하나인지 확인해줘.",
        "문항을 검토해줘. 선택지는 모델이 채워줘.",
        "문항을 검토해줘. 선택지: 1번 4% 감소.",
        "문항을 검토해줘. 선택지: 1번 4% 감소, 1번 원래의 96%.",
        "선택지: 1번 4% 감소, 2번 원래의 96%. 문제만 그대로 보여줘.",
        "선택지 1번과 2번을 검토해줘.",
        REVIEW + " 같은 유형의 새 문항을 만들어줘.",
        REVIEW + " 새로 출제해줘.",
        REVIEW + " 정답은 공개하지 마.",
        REVIEW + " 정답을 말하지는 마.",
        REVIEW + " 정답은 나중에 알려줘.",
        '다음 문장을 그대로 번역해줘: "문항 검토해줘. 선택지 1번 0.96, 2번 0.96."',
    ],
)
async def test_practice_or_incomplete_review_never_discloses_computed_values(prompt):
    output = await check_ncs_answer(ARGUMENTS, _context(prompt))
    assert "0.96" not in output.final_text
    assert "1번" not in output.final_text and "2번" not in output.final_text
    assert not any(character.isdigit() for character in output.final_text)
    assert json.loads(output.content)["answer"] is None


async def test_model_supplied_review_claim_cannot_enable_visible_diagnostics():
    output = await check_ncs_answer({**ARGUMENTS, "userRequested": True}, _context(REVIEW))
    assert output.failed and output.final_text is None
    assert "0.96" not in output.content


async def test_review_does_not_assign_correct_or_incorrect_to_an_explicit_submission():
    prompt = REVIEW.replace("'", "") + " 제 답은 1번입니다."
    output = await check_ncs_answer(ARGUMENTS, _context(prompt))
    data = json.loads(output.content)
    assert data["submission_status"] == "explicit"
    assert data["grading"] == "ambiguous" and data["answer"] is None
    assert "0.96" in output.final_text
    assert "정답입니다" not in output.final_text and "오답입니다" not in output.final_text
    assert "채점은 보류" in output.final_text


@pytest.mark.parametrize("mask", [False, True])
async def test_real_agent_returns_diagnostics_once_after_sanitizing_without_another_hop(
    monkeypatch, mask,
):
    snapshots = []
    _stream(monkeypatch, [{"text": ["UNVERIFIED_DRAFT"], "calls": [
        _call(arguments=json.dumps(ARGUMENTS)),
    ]}], snapshots)

    def sanitize(text):
        return text.replace("0.96", "[MASKED_VALUE]"), text.count("0.96")

    events = [event async for event in agent.run_turn(
        "synthetic/model", [{"role": "user", "content": REVIEW}], [CHECK_NCS_ANSWER],
        _context(REVIEW), preflight_tool="check_ncs_answer", calculation_required=True,
        **({"sanitize_tool_output": sanitize} if mask else {}),
    )]
    text = _text(events)
    assert len(snapshots) == 1
    assert "1번, 2번" in text and "채점은 보류" in text
    assert "UNVERIFIED_DRAFT" not in text
    assert ("[MASKED_VALUE]" if mask else "0.96") in text
    if mask:
        assert "0.96" not in text
    assert [event for event in events if event["type"] == "usage"] == [
        {"type": "usage", "inputTokens": 7, "outputTokens": 11},
    ]
