"""Recompute spelled-out equations (`62만 원 × 36개월 = 2,232만 원`); report wrong ones."""

from __future__ import annotations

import re

#: quantity as written: 2,400만 원, 380만원, 36개월, 0.707, 12%
_NUMBER = (
    r"(\d[\d,]*(?:\.\d+)?)\s*(억|만|천)?\s*(?:원|개월|년|월|일|주|시간|명|석|회|건|대|개|%|배)?"
)
_OP = r"(\+|-|−|×|x|X|\*|÷|/)"
_EQ = r"(=|≈|≒|약)"

_SCALE = {"억": 100_000_000, "만": 10_000, "천": 1_000, None: 1}


def _value(digits: str, scale: str | None) -> float:
    return float(digits.replace(",", "")) * _SCALE[scale]


def _compute(a: float, op: str, b: float) -> float | None:
    if op == "+":
        return a + b
    if op in ("-", "−"):
        return a - b
    if op in ("×", "x", "X", "*"):
        return a * b
    if op in ("÷", "/"):
        return a / b if b else None
    return None


#: term chain before the answer: 「500 + 300 × 2 = 1,100」
_TERM = re.compile(_NUMBER)
_CHAIN = re.compile(rf"{_NUMBER}(?:\s*{_OP}\s*{_NUMBER}){{1,12}}\s*{_EQ}\s*{_NUMBER}")


def _evaluate(values: list[float], ops: list[str]) -> float | None:
    """`values` joined by `ops`, × ÷ before + −."""
    terms, signs = [values[0]], []
    for op, value in zip(ops, values[1:], strict=True):
        if op in ("×", "x", "X", "*", "÷", "/"):
            got = _compute(terms[-1], op, value)
            if got is None:
                return None
            terms[-1] = got
        else:
            terms.append(value)
            signs.append(op)
    total = terms[0]
    for op, value in zip(signs, terms[1:], strict=True):
        total = _compute(total, op, value)
    return total


def findings(text: str, *, where: str = "") -> list[dict[str, str]]:
    """Findings for equations off by >1% after scale words; chains computed whole."""
    out: list[dict[str, str]] = []
    for m in _CHAIN.finditer(text):
        whole = m.group(0)
        eq = list(re.finditer(_EQ, whole))[-1]
        left, right = whole[:eq.start()], whole[eq.end():]
        terms = list(_TERM.finditer(left))
        ops = re.findall(_OP, re.sub(_NUMBER, " ", left))
        answer = _TERM.search(right)
        if answer is None or len(ops) != len(terms) - 1:
            continue
        values = [_value(t.group(1), t.group(2)) for t in terms]
        got = _evaluate(values, ops)
        if got is None:
            continue
        c = _value(answer.group(1), answer.group(2))
        scale = answer.group(2)
        # unscaled operands may be in the answer's scale or plain
        readings = [got]
        if scale and not any(t.group(2) for t in terms):
            readings.append(got * _SCALE[scale])
        if all(abs(r - c) > max(abs(c), 1.0) * 0.01 for r in readings):
            got = readings[-1]
            out.append(
                {
                    "severity": "P0",
                    "rule": "arithmetic",
                    "where": where,
                    "message": (
                        f"「{whole.strip()}」— 식이 맞지 않습니다. "
                        f"계산하면 {_pretty(got, answer.group(2))}입니다."
                    ),
                }
            )
    return out


def _pretty(value: float, scale: str | None) -> str:
    """Number in the document's scale: 3,540만 원, 3.2."""
    if scale in ("만", "억"):
        scaled = value / _SCALE[scale]
        text = f"{scaled:,.1f}".rstrip("0").rstrip(".")
        return f"{text}{scale} 원"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


__all__ = ["findings"]
