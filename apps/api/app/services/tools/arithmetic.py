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
_CHARACTERS = frozenset("0123456789.+-*/()eE \t\r\n")
#: Decimal, optional small-exponent scientific notation.
_NUMBER = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,2})?\Z")
#: Max exponent of scientific notation in a literal (8.0e-6).
_MAX_EXPONENT = 30
#: Past the exact budget (a long compounding period, a fractional base raised high): the
#: expression is evaluated again in decimal with this many significant digits, flagged
#: approximate. Its own bounds refuse pathological sizes.
_APPROX_DIGITS = 50
_MAX_APPROX_EXPONENT = 10_000
#: A result (or step) further from 1 than 10**this is refused, not approximated.
_MAX_APPROX_MAGNITUDE = 100
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
_ZERO_DIVISION_ERROR = json.dumps(
    {**json.loads(_ERROR), "reason": "division_by_zero"}, ensure_ascii=False,
)


class _InvalidCalculation(ValueError):
    pass


class _DivisionByZero(_InvalidCalculation):
    pass


class _BeyondExact(_InvalidCalculation):
    """Valid, but past the exact budget: the decimal pass may still answer it."""


def _bounded(value: Fraction) -> Fraction:
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > _MAX_INTEGER_BITS:
        raise _BeyondExact
    return value


def _sane(value: Decimal) -> Decimal:
    """A decimal-pass step within the magnitude bound (zero is fine)."""
    if not value.is_finite() or (value and abs(value.adjusted()) > _MAX_APPROX_MAGNITUDE):
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

    # Parentheses vanish from AST; cap nesting before parsing.
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
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_AST_NODES:
        raise _InvalidCalculation
    # Full syntax validation before evaluating any division-by-zero branch.
    for node in nodes:
        if not isinstance(node, (
            ast.Expression, ast.Constant, ast.UnaryOp, ast.BinOp,
            ast.UAdd, ast.USub, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow,
        )):
            raise _InvalidCalculation
        if isinstance(node, ast.Constant):
            literal = ast.get_source_segment(source, node) or ""
            if (
                type(node.value) not in (int, float)
                or not _NUMBER.fullmatch(literal)
                or sum(character.isdigit() for character in literal) > _MAX_NUMBER_DIGITS
            ):
                raise _InvalidCalculation
            exponent = re.search(r"[eE]([+-]?[0-9]+)$", literal)
            if exponent and abs(int(exponent.group(1))) > _MAX_EXPONENT:
                raise _InvalidCalculation
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            # Whole-number literal exponent only, checked before evaluation.
            power = node.right
            sign = 1
            if isinstance(power, ast.UnaryOp) and isinstance(power.op, (ast.UAdd, ast.USub)):
                sign = -1 if isinstance(power.op, ast.USub) else 1
                power = power.operand
            if not (
                isinstance(power, ast.Constant)
                and type(power.value) is int
                and abs(sign * power.value) <= _MAX_APPROX_EXPONENT
            ):
                raise _InvalidCalculation

    def evaluate(node: ast.AST, level: int = 0) -> Fraction:
        if level > _MAX_DEPTH:
            raise _InvalidCalculation
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            # AST floats already rounded; recover decimal spelling.
            literal = ast.get_source_segment(source, node) or ""
            return _bounded(Fraction(literal))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand, level + 1)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            base = evaluate(node.left, level + 1)
            power = evaluate(node.right, level + 1)
            if power.denominator != 1:
                raise _InvalidCalculation
            if not base and power.numerator < 0:
                raise _DivisionByZero
            size = max(base.numerator.bit_length(), base.denominator.bit_length())
            if base not in (0, 1, -1) and size * abs(power.numerator) > _MAX_INTEGER_BITS:
                # checked before raising, so the exact pass never builds a huge rational
                raise _BeyondExact
            return _bounded(base ** power.numerator)
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
                if not right:
                    raise _DivisionByZero
                value = left / right
            return _bounded(value)
        raise _InvalidCalculation

    try:
        return source, evaluate(tree.body)
    except _BeyondExact:
        pass
    raise _Approximate(source, _approximate(source, tree.body))


class _Approximate(Exception):
    """The exact pass ran out of budget; carries the decimal pass's value."""

    def __init__(self, source: str, value: Decimal) -> None:
        super().__init__(source)
        self.source = source
        self.value = value


def _approximate(source: str, body: ast.AST) -> Decimal:
    """The same (already validated) expression in decimal with `_APPROX_DIGITS` digits."""
    with localcontext() as context:
        context.prec = _APPROX_DIGITS
        context.Emax = _MAX_APPROX_MAGNITUDE * 2
        context.Emin = -_MAX_APPROX_MAGNITUDE * 2

        def evaluate(node: ast.AST) -> Decimal:
            if isinstance(node, ast.Constant):
                return _sane(Decimal(ast.get_source_segment(source, node) or ""))
            if isinstance(node, ast.UnaryOp):
                value = evaluate(node.operand)
                return -value if isinstance(node.op, ast.USub) else value
            if isinstance(node, ast.BinOp):
                left = evaluate(node.left)
                right = evaluate(node.right)
                if isinstance(node.op, ast.Pow):
                    if right != right.to_integral_value():
                        raise _InvalidCalculation
                    if not left and right < 0:
                        raise _DivisionByZero
                    # magnitude checked before raising: |log10(base)| * exponent
                    if left and abs(left) != 1:
                        size = abs(abs(left).log10() * right)
                        if size > _MAX_APPROX_MAGNITUDE:
                            raise _InvalidCalculation
                    return _sane(left ** int(right))
                if isinstance(node.op, ast.Add):
                    return _sane(left + right)
                if isinstance(node.op, ast.Sub):
                    return _sane(left - right)
                if isinstance(node.op, ast.Mult):
                    return _sane(left * right)
                if isinstance(node.op, ast.Div):
                    if not right:
                        raise _DivisionByZero
                    return _sane(left / right)
            raise _InvalidCalculation

        try:
            return +evaluate(body)
        except ArithmeticError as error:
            raise _InvalidCalculation from error


def _approx_text(value: Decimal, places: int | None) -> str:
    """The decimal pass's value: rounded to `places` when asked, else 15 significant digits."""
    with localcontext() as context:
        context.prec = _APPROX_DIGITS + _MAX_APPROX_MAGNITUDE
        if places is not None:
            rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        else:
            digits = 15 - value.adjusted() - 1 if value else 0
            rounded = value.quantize(Decimal(1).scaleb(-max(digits, 0)), rounding=ROUND_HALF_UP)
            rounded = rounded.normalize() if rounded == rounded.to_integral_value() else rounded
    if rounded == 0:
        rounded = abs(rounded)
    if places is None and abs(rounded.adjusted()) > 20:
        # very large or small: 15 significant digits in scientific notation, not padded zeros
        return f"{rounded:.14E}"
    text = format(rounded, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _exact_decimal(value: Fraction) -> str:
    """Exact finite decimal, else reduced fraction."""
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
    # Precision above operand sizes; half-way rounding stays correct.
    with localcontext() as context:
        context.prec = _MAX_INTEGER_BITS * 2 + 16
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        rounded = decimal.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, "f")


def _value(raw: object) -> Fraction | Decimal:
    """A choice's value: exact, or the decimal pass's when past the exact budget."""
    try:
        return _expression(raw)[1]
    except _Approximate as past:
        return past.value


def _same(choice: Fraction | Decimal, target: Fraction | Decimal) -> bool:
    """Exact equality; with an approximate side, equal to 12 significant digits."""
    if isinstance(choice, Fraction) and isinstance(target, Fraction):
        return choice == target
    def as_decimal(value: Fraction | Decimal) -> Decimal:
        if isinstance(value, Fraction):
            return Decimal(value.numerator) / Decimal(value.denominator)
        return value

    left, right = as_decimal(choice), as_decimal(target)
    if not right:
        return not left
    return abs(left - right) <= abs(right) * Decimal("1e-12")


def _calculate(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or len(arguments) > len(_FIELDS) or set(arguments) - _FIELDS:
        raise _InvalidCalculation
    places = arguments.get("decimal_places")
    if "decimal_places" in arguments and (type(places) is not int or not 0 <= places <= 10):
        raise _InvalidCalculation
    choices = arguments.get("choices")
    if "choices" in arguments:
        if not isinstance(choices, list) or not 2 <= len(choices) <= _MAX_CHOICES:
            raise _InvalidCalculation
    submitted = arguments.get("submitted_choice")
    if "submitted_choice" in arguments and (
        type(submitted) is not int or not choices or not 1 <= submitted <= len(choices)
    ):
        raise _InvalidCalculation

    try:
        evaluated = [_value(choice) for choice in choices] if choices else []
    except _DivisionByZero:
        # Malformed option ≠ evidence of division by zero.
        raise _InvalidCalculation from None
    try:
        expression, exact = _expression(arguments.get("expression"))
        approximate = None
    except _Approximate as past:
        expression, exact, approximate = past.source, None, past.value
    if approximate is None:
        value = _exact_decimal(exact) if places is None else _rounded(exact, places)
        target: Fraction | Decimal = exact if places is None else Fraction(value)
    else:
        value = _approx_text(approximate, places)
        target = approximate if places is None else Decimal(value)

    matches = [index + 1 for index, choice in enumerate(evaluated) if _same(choice, target)]
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
        # an approximate result carries its 50-digit decimal here, flagged below
        "exact": str(exact) if approximate is None else _approx_text(approximate, None),
        "value": value,
        **({"approximate": True, "significant_digits": _APPROX_DIGITS,
            "note": "근삿값입니다(유효숫자 50자리로 계산해 반올림)."}
           if approximate is not None else {}),
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
    except _DivisionByZero:
        return ToolResult(content=_ZERO_DIVISION_ERROR, detail="수치 검산 실패", failed=True)
    except (ValueError, TypeError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return ToolResult(content=_ERROR, detail="수치 검산 실패", failed=True)
    # Numeric result model-only; hidden from step detail (practice answers).
    return ToolResult(content=json.dumps(data, ensure_ascii=False), detail="수치 검산 완료")


CALCULATE = Tool(
    name="calculate",
    description=(
        "사칙연산과 정수 거듭제곱을 정확히 계산하고 숫자 선지·제출 답을 대조합니다. "
        "복리처럼 지수가 크거나 값이 정확히 다루기에 너무 크면 유효숫자 50자리 근삿값을 "
        "approximate: true로 돌려줍니다. "
        "숫자(8.0e-6 같은 지수 표기 가능), + - * /, ** (정수 지수), 괄호만 사용하세요. "
        "단위는 제외하고 퍼센트는 *100을 명시하세요. "
        "decimal_places는 문제에 반올림 지시가 있을 때만 지정하세요. "
        "문제 조건과 식이 맞는지 판단하거나 일반 코드를 실행하지 않습니다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "maxLength": _MAX_EXPRESSION_CHARS,
                "description": (
                    "Numeric expression using + - * /, ** with a whole exponent, "
                    "scientific notation and parentheses; no units or %."
                ),
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
