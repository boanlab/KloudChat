"""The NCS check reports actual arithmetic separately from model classification."""

from __future__ import annotations

import json

import pytest

from app.services.tools import arithmetic, ncs_check
from app.services.tools.base import ToolContext
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
    output = await check_ncs_answer(
        arguments,
        ToolContext(
            user_id="learner", session_id="practice", request=f"제 답은 {submitted}번입니다"
        ),
    )
    result = json.loads(output.content)
    assert not output.failed
    assert len(calls) == 1
    assert calls[0] == {key: value for key, value in arguments.items() if key != "decision"}
    assert result["decision"] == "calculate"
    assert result["arithmetic_verified"] is True
    assert result["submission_status"] == "explicit"
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
        },
        ToolContext(user_id="learner", session_id="practice", request="제 답은 2번입니다"),
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


async def test_missing_conditions_end_with_a_fixed_message_not_the_models_reason():
    output = await check_ncs_answer(
        {
            "decision": "needs_input",
            "reason": "secret-canary@example.com 값은 82.5이며 정답은 3번이라고 주장함",
        }
    )
    assert getattr(output, "final_text", None) == (
        "필요한 조건이 부족하여 정답이나 채점을 확정할 수 없습니다. "
        "문제의 누락된 조건을 보완해 주세요."
    )
    assert "secret-canary" not in output.final_text
    assert not any(character.isdigit() for character in output.final_text)
    assert json.loads(output.content)["arithmetic_verified"] is False
    assert not output.failed


@pytest.mark.parametrize("choices", [["79", "82.5"], ["80", "160/2"]])
async def test_non_unique_match_ends_without_disclosing_values_or_blame(choices):
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "1600/20", "choices": choices}
    )
    assert getattr(output, "final_text", None) == (
        "계산기에 입력된 식과 선지에서 유일한 정답을 확인하지 못했습니다. "
        "채점을 보류하고 문제 조건·계산식·단위·선지를 다시 확인해야 합니다."
    )
    assert not any(character.isdigit() for character in output.final_text)
    result = json.loads(output.content)
    assert result["arithmetic_verified"] is True
    assert result["answer"] is None
    assert "모델" in result["scope"]
    assert "식 작성" in result["scope"]
    assert not output.failed


@pytest.mark.parametrize(
    "arguments",
    [
        {"decision": "not_applicable", "reason": "비수리 문항임"},
        {"decision": "calculate", "expression": "1600/20", "choices": ["80", "82.5"]},
        {"decision": "calculate", "expression": "1600/20"},
        {"decision": "calculate", "expression": "1/0"},
        {"decision": "needs_input", "reason": ""},
    ],
)
async def test_other_paths_leave_the_follow_up_policy_unchanged(arguments):
    output = await check_ncs_answer(arguments)
    assert getattr(output, "final_text", None) is None


@pytest.mark.parametrize("invented", [1, 3, "1", True, 99, None, {"claim": "secret-canary"}])
async def test_a_model_invented_submission_cannot_trigger_grading(invented):
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": "1600/20",
            "choices": ["80", "82.5"],
            "submitted_choice": invented,
        },
        ToolContext(
            user_id="learner",
            session_id="practice",
            request="가중 평균을 구하세요. 1번은 80이고 2번은 82.5입니다.",
        ),
    )
    data = json.loads(output.content)
    assert not output.failed
    assert data["submission_status"] == "not_provided"
    assert data["grading"] == "not_requested"
    assert data["answer"] == 1
    assert "secret-canary" not in output.content


@pytest.mark.parametrize("model_arguments", [{}, {"submitted_choice": 1}])
async def test_the_actual_user_selection_overrides_an_omitted_or_invented_model_choice(
    model_arguments,
):
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": "1600/20",
            "choices": ["80", "82.5"],
            **model_arguments,
        },
        ToolContext(user_id="learner", session_id="practice", request="제 답은 2번입니다"),
    )
    data = json.loads(output.content)
    assert data["submission_status"] == "explicit"
    assert data["grading"] == "incorrect"


async def test_missing_context_never_trusts_the_models_submitted_choice():
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": "1600/20",
            "choices": ["80", "82.5"],
            "submitted_choice": 1,
        }
    )
    data = json.loads(output.content)
    assert data["submission_status"] == "not_provided"
    assert data["grading"] == "not_requested"


@pytest.mark.parametrize(
    "optional",
    [{"choices": None}, {"decimal_places": None}, {"choices": None, "decimal_places": None}],
)
async def test_null_optional_arguments_are_omitted_before_calculation(monkeypatch, optional):
    original = arithmetic.calculate
    calls = []

    async def counted(arguments):
        calls.append(arguments)
        return await original(arguments)

    monkeypatch.setattr(arithmetic, "calculate", counted)
    output = await check_ncs_answer({"decision": "calculate", "expression": "1/3", **optional})
    result = json.loads(output.content)
    assert not output.failed
    assert calls == [{"expression": "1/3"}]
    assert result["exact"] == result["value"] == "1/3"
    assert result["choice_status"] == "not_requested"
    assert result["grading"] == "not_requested"


async def test_null_rounding_does_not_change_the_valid_choices_or_exact_matching():
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": "1/3",
            "choices": ["0.33", "1/3"],
            "decimal_places": None,
        }
    )
    result = json.loads(output.content)
    assert not output.failed
    assert result["answer"] == 2
    assert result["value"] == "1/3"


async def test_null_expression_is_not_treated_as_an_optional_omission():
    output = await check_ncs_answer({"decision": "calculate", "expression": None})
    assert output.failed
    assert json.loads(output.content)["error"] == "invalid_calculation"


@pytest.mark.parametrize(
    "choices,expected",
    [
        (["80", "82.5"], ["80", "82.5"]),
        ([80, 81, 83, 85], ["80", "81", "83", "85"]),
        ([80, "82.5"], ["80", "82.5"]),
        ("[80, 82.5]", ["80", "82.5"]),
        ('["80", "82.5"]', ["80", "82.5"]),
        ("[80.000, 82.500]", ["80.000", "82.500"]),
    ],
)
async def test_choices_adapter_preserves_bounded_arrays_and_exact_json_literals(
    monkeypatch, choices, expected
):
    original = arithmetic.calculate
    calls = []

    async def counted(arguments):
        calls.append(arguments)
        return await original(arguments)

    monkeypatch.setattr(arithmetic, "calculate", counted)
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "1600/20", "choices": choices}
    )
    assert not output.failed
    assert calls == [{"expression": "1600/20", "choices": expected}]
    result = json.loads(output.content)
    assert result["answer"] == 1
    assert result["value"] == "80"


async def test_encoded_decimal_choices_do_not_take_a_binary_float_round_trip():
    literal = "0.12345678901234567890123456789"
    output = await check_ncs_answer(
        {
            "decision": "calculate",
            "expression": literal,
            "choices": f"[{literal}, 0.12345678901234568]",
        }
    )
    result = json.loads(output.content)
    assert not output.failed
    assert result["answer"] == 1
    assert result["matched_choices"] == [1]
    assert result["value"] == literal


@pytest.mark.parametrize(
    "choices",
    [
        [True, 80],
        [False, "80"],
        [80.0, "82.5"],
        [float("nan"), "80"],
        [float("inf"), "80"],
        [["80"], "82.5"],
        [{"value": "80"}, "82.5"],
        [None, "80"],
        ("80", "82.5"),
        {"1": "80", "2": "82.5"},
        [],
        ["80"],
        ["80"] * 11,
        ["", "80"],
        ["1" * 1025, "80"],
        "80, 82.5",
        "secret-canary",
        "[80,82.5] secret-canary",
        '{"1":80,"2":82.5}',
        '"[80,82.5]"',
        "[true,80]",
        "[false,80]",
        "[null,80]",
        "[[80],82.5]",
        "[NaN,80]",
        "[Infinity,80]",
        "[]",
        "[80]",
        "[" + ",".join(["80"] * 11) + "]",
        '["' + "1" * 1025 + '", "80"]',
        "[" * 3000 + "80" + "]" * 3000,
    ],
)
async def test_invalid_choices_shape_is_actionable_and_never_runs_arithmetic(monkeypatch, choices):
    async def forbidden(_arguments):
        pytest.fail("an invalid choices format reached arithmetic")

    monkeypatch.setattr(arithmetic, "calculate", forbidden)
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "80", "choices": choices}
    )
    result = json.loads(output.content)
    assert output.failed
    assert result["arithmetic_verified"] is False
    assert result["error"] == "invalid_ncs_choices"
    assert 'choices=["80","82.5"]' in result["message"]
    assert "secret-canary" not in output.content


@pytest.mark.parametrize(
    "choices",
    ["[" + " " * 10240 + "80,82.5]", json.dumps(["가" * 4000, "80"], ensure_ascii=False)],
)
async def test_encoded_choices_byte_budget_is_checked_before_json_parsing(monkeypatch, choices):
    original = json.loads

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized choices reached the JSON parser")

    monkeypatch.setattr(ncs_check.json, "loads", forbidden)
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "80", "choices": choices}
    )
    assert output.failed
    assert original(output.content)["error"] == "invalid_ncs_choices"


async def test_an_encoded_array_at_the_byte_limit_is_still_accepted():
    encoded = "[80,82.5]"
    encoded += " " * (10240 - len(encoded))
    assert len(encoded.encode("utf-8")) == 10240
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "80", "choices": encoded}
    )
    assert not output.failed
    assert json.loads(output.content)["answer"] == 1


async def test_a_huge_python_integer_is_rejected_before_string_conversion():
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "80", "choices": [10**5000, 80]}
    )
    assert output.failed
    assert json.loads(output.content)["error"] == "invalid_ncs_choices"


@pytest.mark.parametrize(
    "choices",
    [
        ["__import__('secret-canary')", "80"],
        '["__import__(\\"secret-canary\\")", "80"]',
        ["1/0", "80"],
        ["1+", "80"],
        ["9" * 65, "80"],
        [10**64, 80],
        "[1e2,80]",
        ["(" * 40 + "1" + ")" * 40, "80"],
    ],
)
async def test_normalized_choices_still_obey_the_original_arithmetic_limits(choices):
    output = await check_ncs_answer(
        {"decision": "calculate", "expression": "80", "choices": choices}
    )
    assert output.failed
    result = json.loads(output.content)
    assert result["error"] == "invalid_calculation"
    assert result["arithmetic_verified"] is False
    assert "secret-canary" not in output.content


def test_tool_contract_is_portable_read_only_and_discloses_no_answer_in_labels():
    assert CHECK_NCS_ANSWER.name == "check_ncs_answer"
    assert CHECK_NCS_ANSWER.run is check_ncs_answer
    assert CHECK_NCS_ANSWER.read_only and CHECK_NCS_ANSWER.wants_context
    assert CHECK_NCS_ANSWER.title == "문항 검산"
    assert CHECK_NCS_ANSWER.label == "문항 검산 중"
    assert CHECK_NCS_ANSWER.parameters["required"] == ["decision"]
    assert CHECK_NCS_ANSWER.parameters["additionalProperties"] is False
    assert "oneOf" not in CHECK_NCS_ANSWER.parameters
    assert CHECK_NCS_ANSWER.parameters["properties"]["reason"]["maxLength"] == 500
    assert "submitted_choice" not in CHECK_NCS_ANSWER.parameters["properties"]
    assert CHECK_NCS_ANSWER.parameters["properties"]["choices"]["type"] == "array"
    assert CHECK_NCS_ANSWER.parameters["properties"]["decimal_places"]["type"] == "integer"
    assert arithmetic.CALCULATE.parameters["properties"]["choices"]["type"] == "array"
    assert "submitted_choice" in arithmetic.CALCULATE.parameters["properties"]
    assert ncs_check.__name__ == "app.services.tools.ncs_check"
