"""Bounded exact arithmetic, with no source execution, I/O or caller credentials."""

from __future__ import annotations

import ast
import json
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from typing import Any

from app.services.tools.base import Tool, ToolResult

_MAX_EXPRESSION_CHARS = 1024
_MAX_NUMBER_DIGITS = 64
_MAX_AST_NODES = 128
_MAX_DEPTH = 32
_MAX_INTEGER_BITS = 512
_MAX_CHOICES = 10
_FIELDS = frozenset({"expression", "choices", "submitted_choice", "decimal_places"})
_CHARACTERS = frozenset("0123456789.+-*/() \t\r\n")
_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_SCOPE = (
    "입력한 식의 산술과 선지 일치만 검증했습니다. "
    "식이 문제의 조건과 단위에 맞는지는 별도로 확인해야 합니다."
)
_ERROR = json.dumps(
    {
        "error": "invalid_calculation",
        "message": (
            "계산할 수 없는 입력입니다. 길이가 제한된 숫자·사칙연산·괄호만 사용하고, "
            "0 나눗셈과 선지·제출 번호·반올림 자리 설정을 확인하세요."
        ),
    },
    ensure_ascii=False,
)


class _InvalidCalculation(ValueError):
    pass


def _bounded(value: Fraction) -> Fraction:
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > _MAX_INTEGER_BITS:
        raise _InvalidCalculation
    return value


def _expression(raw: object) -> tuple[str, Fraction]:
    if not isinstance(raw, str) or not 0 < len(raw) <= _MAX_EXPRESSION_CHARS:
        raise _InvalidCalculation
    if any(character not in _CHARACTERS for character in raw):
        raise _InvalidCalculation
    source = raw.strip()
    if not source:
        raise _InvalidCalculation

    # Parentheses disappear from the AST; cap their nesting before parsing too.
    depth = 0
    for character in source:
        if character == "(":
            depth += 1
            if depth > _MAX_DEPTH:
                raise _InvalidCalculation
        elif character == ")":
            depth -= 1
            if depth < 0:
                raise _InvalidCalculation
    if depth:
        raise _InvalidCalculation
    tree = ast.parse(source, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise _InvalidCalculation

    def evaluate(node: ast.AST, level: int = 0) -> Fraction:
        if level > _MAX_DEPTH:
            raise _InvalidCalculation
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            # AST floats have already rounded: recover the original decimal spelling.
            literal = ast.get_source_segment(source, node) or ""
            if (
                not _NUMBER.fullmatch(literal)
                or sum(character.isdigit() for character in literal) > _MAX_NUMBER_DIGITS
            ):
                raise _InvalidCalculation
            return _bounded(Fraction(literal))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand, level + 1)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = evaluate(node.left, level + 1)
            right = evaluate(node.right, level + 1)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            else:
                value = left / right
            return _bounded(value)
        raise _InvalidCalculation

    return source, evaluate(tree.body)


def _exact_decimal(value: Fraction) -> str:
    """A finite decimal when exact, otherwise the exact reduced fraction."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        twos += 1
        denominator //= 2
    while denominator % 5 == 0:
        fives += 1
        denominator //= 5
    if denominator != 1:
        return str(value)
    places = max(twos, fives)
    if not places:
        return str(value.numerator)
    numerator = abs(value.numerator) * 2 ** (places - twos) * 5 ** (places - fives)
    digits = str(numerator).rjust(places + 1, "0")
    decimal = f"{digits[:-places]}.{digits[-places:]}".rstrip("0").rstrip(".")
    return ("-" if value < 0 else "") + decimal


def _rounded(value: Fraction, places: int) -> str:
    # Operand sizes are capped. This precision exceeds both denominator and
    # integer digits, so a rational adjacent to a half-way point stays on its side.
    with localcontext() as context:
        context.prec = _MAX_INTEGER_BITS * 2 + 16
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        rounded = decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, "f")


def _calculate(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or len(arguments) > len(_FIELDS) or set(arguments) - _FIELDS:
        raise _InvalidCalculation
    expression, exact = _expression(arguments.get("expression"))
    places = arguments.get("decimal_places")
    if "decimal_places" in arguments and (type(places) is not int or not 0 <= places <= 10):
        raise _InvalidCalculation
    value = _exact_decimal(exact) if places is None else _rounded(exact, places)
    target = exact if places is None else Fraction(value)

    choices = arguments.get("choices")
    if "choices" in arguments:
        if not isinstance(choices, list) or not 2 <= len(choices) <= _MAX_CHOICES:
            raise _InvalidCalculation
        evaluated = [_expression(choice)[1] for choice in choices]
    else:
        evaluated = []
    submitted = arguments.get("submitted_choice")
    if "submitted_choice" in arguments and (
        type(submitted) is not int or not choices or not 1 <= submitted <= len(choices)
    ):
        raise _InvalidCalculation

    matches = [index + 1 for index, choice in enumerate(evaluated) if choice == target]
    if choices is None:
        status = "not_requested"
    elif len(matches) == 1:
        status = "unique"
    else:
        status = "ambiguous" if matches else "no_match"
    answer = matches[0] if status == "unique" else None
    if submitted is None:
        grading = "not_requested"
    elif status != "unique":
        grading = status
    else:
        grading = "correct" if submitted == answer else "incorrect"
    return {
        "expression": expression,
        "exact": str(exact),
        "value": value,
        "decimal_places": places,
        "choice_status": status,
        "matched_choices": matches,
        "answer": answer,
        "grading": grading,
        "scope": _SCOPE,
    }


async def calculate(arguments: dict[str, Any]) -> ToolResult:
    try:
        data = _calculate(arguments)
    except (ValueError, TypeError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return ToolResult(content=_ERROR, detail="수치 검산 실패", failed=True)
    # A practice question may be checked before its learner answers. Keep the
    # numeric result in model-only content, never in the visible step detail.
    return ToolResult(content=json.dumps(data, ensure_ascii=False), detail="수치 검산 완료")


CALCULATE = Tool(
    name="calculate",
    description=(
        "사칙연산을 정확히 계산하고 숫자 선지·제출 답을 대조합니다. "
        "숫자, + - * /, 괄호만 사용하세요. 단위는 제외하고 퍼센트는 *100을 명시하세요. "
        "decimal_places는 문제에 반올림 지시가 있을 때만 지정하세요. "
        "문제 조건과 식이 맞는지 판단하거나 일반 코드를 실행하지 않습니다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "maxLength": _MAX_EXPRESSION_CHARS,
                "description": "Numeric expression using + - * / and parentheses; no units or %.",
            },
            "choices": {
                "type": "array",
                "minItems": 2,
                "maxItems": _MAX_CHOICES,
                "items": {"type": "string", "maxLength": _MAX_EXPRESSION_CHARS},
                "description": "Optional numeric choices in their original order, without labels.",
            },
            "submitted_choice": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_CHOICES,
                "description": "Optional 1-based submitted answer; requires choices.",
            },
            "decimal_places": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "Only for explicit HALF_UP rounding; choices stay exact.",
            },
        },
        "required": ["expression"],
        "additionalProperties": False,
    },
    run=calculate,
    label="수치 계산 중",
    title="수치 계산",
    read_only=True,
)
