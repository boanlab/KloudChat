"""Arithmetic is executed locally; neither model prose nor a submitted answer is trusted."""

from __future__ import annotations

import ast
import json
from fractions import Fraction

import pytest

from app.services.tools.arithmetic import CALCULATE, calculate


async def _result(**arguments):
    output = await calculate(arguments)
    assert not output.failed, output.content
    return json.loads(output.content), output


@pytest.mark.parametrize(
    ("expression", "exact", "value"),
    [
        ("1600 / 20", "80", "80"),
        ("(12 * 70 + 8 * 95) / (12 + 8)", "80", "80"),
        ("(125 - 100) / 100 * 100", "25", "25"),
        ("(70 + 95) / 2", "165/2", "82.5"),
        ("0.1 + 0.2", "3/10", "0.3"),
        (
            "0.12345678901234567890123456789 + 0.00000000000000000000000000001",
            "1234567890123456789012345679/10000000000000000000000000000",
            "0.1234567890123456789012345679",
        ),
        ("1 / 3", "1/3", "1/3"),
        ("-5 + +2", "-3", "-3"),
        ("2 * (3 + 4) - 5 / 2", "23/2", "11.5"),
        (".5 + 1.", "3/2", "1.5"),
        ("0 / 8", "0", "0"),
        ("  1 + 2  ", "3", "3"),
    ],
)
async def test_arithmetic_keeps_the_original_decimal_values(expression, exact, value):
    data, output = await _result(expression=expression)
    assert data["expression"] == expression.strip()
    assert data["exact"] == exact
    assert data["value"] == value
    assert data["choice_status"] == "not_requested"
    assert data["matched_choices"] == []
    assert data["answer"] is None
    assert data["grading"] == "not_requested"
    assert "식" in data["scope"] and "문제" in data["scope"]
    assert output.detail == "수치 검산 완료"


async def test_weighted_average_does_not_match_the_simple_average_distractor():
    data, output = await _result(
        expression="(12 * 70 + 8 * 95) / 20",
        choices=["80", "81", "82.5", "85"],
        submitted_choice=3,
    )
    assert data["exact"] == "80"
    assert data["choice_status"] == "unique"
    assert data["matched_choices"] == [1]
    assert data["answer"] == 1
    assert data["grading"] == "incorrect"
    assert output.detail == "수치 검산 완료"


@pytest.mark.parametrize("submitted_choice,grading", [(1, "incorrect"), (2, "correct")])
async def test_grading_is_computed_from_the_value_not_the_learners_claim(submitted_choice, grading):
    data, _ = await _result(
        expression="(125 - 100) / 100 * 100",
        choices=["20", "25", "30", "50"],
        submitted_choice=submitted_choice,
    )
    assert data["answer"] == 2
    assert data["grading"] == grading


@pytest.mark.parametrize(
    "choices,status,matches",
    [
        (["80", "160/2", "82.5", "85"], "ambiguous", [1, 2]),
        (["70", "75", "82.5", "85"], "no_match", []),
    ],
)
async def test_an_invalid_question_cannot_be_graded(choices, status, matches):
    data, _ = await _result(expression="1600/20", choices=choices, submitted_choice=1)
    assert data["choice_status"] == status
    assert data["matched_choices"] == matches
    assert data["answer"] is None
    assert data["grading"] == status


async def test_choice_matching_without_a_submitted_answer_does_not_grade():
    data, _ = await _result(expression="1/3", choices=["0.3333333333", "1/3"])
    assert data["answer"] == 2
    assert data["grading"] == "not_requested"


@pytest.mark.parametrize(
    "expression,places,value",
    [
        ("1/3", 2, "0.33"),
        ("2.675", 2, "2.68"),
        ("-2.675", 2, "-2.68"),
        ("2.5", 0, "3"),
        ("-2.5", 0, "-3"),
        ("1/8", 10, "0.1250000000"),
    ],
)
async def test_rounding_is_explicit_half_up(expression, places, value):
    data, _ = await _result(expression=expression, decimal_places=places)
    assert data["exact"] == str(Fraction(expression))
    assert data["value"] == value
    assert data["decimal_places"] == places


async def test_only_the_computed_answer_is_rounded_not_a_different_choice():
    data, _ = await _result(
        expression="1/3", decimal_places=2, choices=["0.3301", "0.33"], submitted_choice=1
    )
    assert data["matched_choices"] == [2]
    assert data["grading"] == "incorrect"


async def test_no_implicit_rounding_is_used_to_find_a_choice():
    data, _ = await _result(expression="1/3", choices=["0.33", "0.3333333333333333"])
    assert data["choice_status"] == "no_match"
    assert data["answer"] is None


@pytest.mark.parametrize(
    "count_a,value_a,count_b,value_b",
    [(1, 10, 9, 90), (7, 12, 3, 30), (11, 70, 9, 95), (2, -10, 5, 18), (3, 0, 4, 5)],
)
async def test_weighted_mean_is_not_specific_to_the_reported_question(
    count_a, value_a, count_b, value_b
):
    expression = f"({count_a}*{value_a}+{count_b}*{value_b})/({count_a}+{count_b})"
    expected = Fraction(count_a * value_a + count_b * value_b, count_a + count_b)
    data, _ = await _result(expression=expression)
    assert data["exact"] == str(expected)


@pytest.mark.parametrize("sign,expected", [("-", "2.67"), ("+", "2.68")])
async def test_rounding_does_not_erase_which_side_of_a_halfway_point_the_value_is_on(
    sign, expected
):
    data, _ = await _result(expression=f"2.675 {sign} 1 / ({'9' * 64})", decimal_places=2)
    assert data["value"] == expected


async def test_rounded_zero_is_not_presented_as_a_negative_value():
    data, _ = await _result(expression="-1/1000", decimal_places=2)
    assert data["exact"] == "-1/1000"
    assert data["value"] == "0.00"


async def test_large_but_bounded_integer_arithmetic_stays_exact():
    number = "9" * 64
    data, _ = await _result(expression=f"{number} * {number}")
    assert data["exact"] == data["value"] == str(int(number) * int(number))


async def test_ten_choices_are_allowed_and_the_last_can_be_selected():
    data, output = await _result(
        expression="5+5", choices=[str(number) for number in range(1, 11)], submitted_choice=10
    )
    assert data["answer"] == 10
    assert data["grading"] == "correct"
    assert output.detail == "수치 검산 완료"


@pytest.mark.parametrize(
    "expression",
    [
        "",
        " ",
        "1/0",
        "1/(2-2)",
        "NaN",
        "inf",
        "Infinity",
        "True",
        "False",
        "__import__('os').system('echo unsafe')",
        "open('/tmp/unsafe')",
        "(1).__class__",
        "[1][0]",
        "{'a':1}",
        "1 if 1 else 0",
        "sum([1,2])",
        "lambda: 1",
        "1;2",
        "1e3",
        "1E-3",
        "0x10",
        "0b10",
        "1_000",
        "20%",
        "2**3",
        "7//2",
        "3j",
        "1,000",
        "1=1",
        "1+",
        "(1+2",
        "1+2)",
        "12원",
        "１＋２",
        "1\x00+2",
        "1\u00a0+2",
        "# secret-canary\n1+2",
    ],
)
async def test_non_arithmetic_or_invalid_expressions_fail_safely(expression):
    output = await calculate({"expression": expression})
    assert output.failed
    data = json.loads(output.content)
    assert data["error"] == "invalid_calculation"
    assert "exact" not in data and "answer" not in data
    assert "secret-canary" not in output.content
    assert output.detail == "수치 검산 실패"


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        "1+2",
        {},
        {"expression": 3},
        {"expression": True},
        {"expression": "1+2", "extra": "secret-canary"},
        {"expression": "1", "choices": "1,2"},
        {"expression": "1", "choices": []},
        {"expression": "1", "choices": ["1"]},
        {"expression": "1", "choices": ["1"] * 11},
        {"expression": "1", "choices": [1, 2]},
        {"expression": "1", "choices": ["1", True]},
        {"expression": "1", "choices": ["1", ""]},
        {"expression": "1", "choices": ["1", "20%"]},
        {"expression": "1", "choices": ["1", "1/0"]},
        {"expression": "1", "choices": None},
        {"expression": "1", "submitted_choice": 1},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": 0},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": 3},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": True},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": 1.0},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": "1"},
        {"expression": "1", "choices": ["1", "2"], "submitted_choice": None},
        {"expression": "1", "decimal_places": True},
        {"expression": "1", "decimal_places": -1},
        {"expression": "1", "decimal_places": 11},
        {"expression": "1", "decimal_places": 2.0},
        {"expression": "1", "decimal_places": None},
    ],
)
async def test_the_runtime_validates_the_schema_instead_of_trusting_the_model(arguments):
    output = await calculate(arguments)
    assert output.failed
    assert json.loads(output.content)["error"] == "invalid_calculation"
    assert "secret-canary" not in output.content


@pytest.mark.parametrize(
    "expression",
    [
        "1" + " " * 1024,
        "9" * 65,
        "0." + "1" * 65,
        "(" * 40 + "1" + ")" * 40,
        "+" * 40 + "1",
        "+".join(["1"] * 80),
        "*".join(["9" * 64] * 4),
        "1/" + "/".join(["9" * 64] * 4),
    ],
)
async def test_input_and_intermediate_work_are_bounded(expression):
    output = await calculate({"expression": expression})
    assert output.failed
    assert json.loads(output.content)["error"] == "invalid_calculation"


async def test_every_choice_obeys_the_same_input_and_work_bounds():
    output = await calculate({"expression": "1", "choices": ["1", "9" * 65]})
    assert output.failed


async def test_the_calculator_never_opens_network_files_or_processes(monkeypatch):
    import asyncio
    import builtins
    import os
    import socket
    import subprocess

    import httpx

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("Arithmetic may not perform I/O or execute source code")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "eval", forbidden)
        patch.setattr(builtins, "exec", forbidden)
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "system", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(asyncio, "create_subprocess_exec", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(httpx.AsyncClient, "request", forbidden)
        output = await calculate({"expression": "(12*70+8*95)/20"})
        rejected = await calculate({"expression": "__import__('os').system('unsafe')"})
    assert calls == []
    assert not output.failed and rejected.failed


def test_tool_contract_is_read_only_and_needs_no_caller_credentials():
    assert CALCULATE.name == "calculate"
    assert CALCULATE.read_only
    assert not CALCULATE.wants_context
    assert CALCULATE.run is calculate
    assert CALCULATE.label.endswith("중")
    assert CALCULATE.title and not CALCULATE.title.endswith("중")
    assert CALCULATE.parameters["required"] == ["expression"]
    assert CALCULATE.parameters["additionalProperties"] is False


def test_source_cannot_call_eval_exec_compile_or_import_external_clients():
    from pathlib import Path

    import app.services.tools.arithmetic as arithmetic

    tree = ast.parse(Path(arithmetic.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "open"}
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            assert not any(
                name.split(".")[0] in {"httpx", "socket", "subprocess", "os"} for name in names
            )
