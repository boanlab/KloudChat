"""The NCS check reports actual arithmetic separately from model classification."""

from __future__ import annotations

import json

import pytest

from app.services.tools import arithmetic, ncs_check
from app.services.tools.ncs_check import CHECK_NCS_ANSWER, check_ncs_answer


@pytest.mark.parametrize(
    "choices,submitted,status,matches,answer,grading",
    [
        (["80", "81", "82.5", "85"], 3, "unique", [1], 1, "incorrect"),
        (["80", "81", "82.5", "85"], 1, "unique", [1], 1, "correct"),
        (["80", "160/2", "82.5", "85"], 1, "ambiguous", [1, 2], None, "ambiguous"),
        (["70", "75", "82.5", "85"], 1, "no_match", [], None, "no_match"),
    ],
)
async def test_gate_preserves_actual_arithmetic_and_grading(
    monkeypatch, choices, submitted, status, matches, answer, grading
):
    original = arithmetic.calculate
    calls = []

    async def counted(arguments):
        calls.append(arguments)
        return await original(arguments)

    monkeypatch.setattr(arithmetic, "calculate", counted)
    arguments = {
        "decision": "calculate",
        "expression": "(12*70+8*95)/20",
        "choices": choices,
        "submitted_choice": submitted,
    }
    output = await check_ncs_answer(arguments)
    result = json.loads(output.content)
    assert not output.failed
    assert len(calls) == 1
    assert calls[0] == {key: value for key, value in arguments.items() if key != "decision"}
    assert result["decision"] == "calculate"
    assert result["arithmetic_verified"] is True
    assert result["exact"] == result["value"] == "80"
    assert result["choice_status"] == status
    assert result["matched_choices"] == matches
    assert result["answer"] == answer
    assert result["grading"] == grading
    assert output.detail == "문항 검산 완료"
    assert "문제" in result["scope"]


async def test_gate_preserves_explicit_rounding_and_exact_fraction():
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": "1/3",
            "choices": ["0.3301", "0.33"],
            "submitted_choice": 2,
            "decimal_places": 2,
        }
    )
    result = json.loads(output.content)
    assert result["exact"] == "1/3"
    assert result["value"] == "0.33"
    assert result["decimal_places"] == 2
    assert result["answer"] == 2
    assert result["grading"] == "correct"


@pytest.mark.parametrize("decision", ["needs_input", "not_applicable"])
async def test_non_arithmetic_decision_never_calls_the_calculator(monkeypatch, decision):
    calls = []

    async def counted(_arguments):
        calls.append(True)
        raise AssertionError("This decision must not run arithmetic")

    monkeypatch.setattr(arithmetic, "calculate", counted)
    output = await check_ncs_answer({"decision": decision, "reason": " 조건을 먼저 확인함 "})
    result = json.loads(output.content)
    assert not output.failed
    assert calls == []
    assert result["decision"] == decision
    assert result["reason"] == "조건을 먼저 확인함"
    assert result["arithmetic_verified"] is False
    assert "exact" not in result and "answer" not in result and "grading" not in result
    assert output.detail == "문항 검산 미완료"
    assert "모델" in result["scope"]


@pytest.mark.parametrize("expression", ["1/0", "__import__('secret-canary')", "9" * 65])
async def test_a_failed_calculation_is_not_reported_as_verified(expression):
    output = await check_ncs_answer({"decision": "calculate", "expression": expression})
    result = json.loads(output.content)
    assert output.failed
    assert result["arithmetic_verified"] is False
    assert result["decision"] == "calculate"
    assert result["error"] == "invalid_calculation"
    assert "secret-canary" not in output.content
    assert output.detail == "문항 검산 미완료"


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        "calculate",
        {},
        {"decision": None},
        {"decision": True},
        {"decision": ["calculate"]},
        {"decision": "secret-canary"},
        {"decision": " calculate", "expression": "1"},
        {"decision": "calculate", "expression": "1", "reason": "secret-canary"},
        {"decision": "calculate", "expression": "1", "extra": "secret-canary"},
        {"decision": "needs_input"},
        {"decision": "needs_input", "reason": ""},
        {"decision": "needs_input", "reason": " \t\n"},
        {"decision": "needs_input", "reason": None},
        {"decision": "needs_input", "reason": 123},
        {"decision": "needs_input", "reason": "a" * 501},
        {"decision": "not_applicable", "reason": "조건 확인", "expression": "1"},
        {"decision": "not_applicable", "reason": "조건 확인", "choices": ["1", "2"]},
        {"decision": "not_applicable", "reason": "조건 확인", "submitted_choice": 1},
        {"decision": "not_applicable", "reason": "조건 확인", "decimal_places": 0},
        {"decision": "needs_input", "reason": "조건 확인", "extra": "secret-canary"},
    ],
)
async def test_invalid_gate_structure_never_runs_arithmetic_or_echoes_input(monkeypatch, arguments):
    calls = []

    async def counted(_arguments):
        calls.append(True)
        raise AssertionError("An invalid gate must not run arithmetic")

    monkeypatch.setattr(arithmetic, "calculate", counted)
    output = await check_ncs_answer(arguments)
    result = json.loads(output.content)
    assert calls == []
    assert output.failed
    assert result["error"] == "invalid_ncs_check"
    assert result["arithmetic_verified"] is False
    assert "secret-canary" not in output.content
    assert output.detail == "문항 검산 미완료"


async def test_reason_accepts_exactly_the_documented_bound():
    output = await check_ncs_answer({"decision": "needs_input", "reason": "가" * 500})
    assert not output.failed
    assert len(json.loads(output.content)["reason"]) == 500


async def test_missing_expression_reuses_the_safe_arithmetic_failure():
    output = await check_ncs_answer({"decision": "calculate"})
    assert output.failed
    assert json.loads(output.content)["error"] == "invalid_calculation"


def test_tool_contract_is_portable_read_only_and_discloses_no_answer_in_labels():
    assert CHECK_NCS_ANSWER.name == "check_ncs_answer"
    assert CHECK_NCS_ANSWER.run is check_ncs_answer
    assert CHECK_NCS_ANSWER.read_only and not CHECK_NCS_ANSWER.wants_context
    assert CHECK_NCS_ANSWER.title == "문항 검산"
    assert CHECK_NCS_ANSWER.label == "문항 검산 중"
    assert CHECK_NCS_ANSWER.parameters["required"] == ["decision"]
    assert CHECK_NCS_ANSWER.parameters["additionalProperties"] is False
    assert "oneOf" not in CHECK_NCS_ANSWER.parameters
    assert CHECK_NCS_ANSWER.parameters["properties"]["reason"]["maxLength"] == 500
    assert ncs_check.__name__ == "app.services.tools.ncs_check"
