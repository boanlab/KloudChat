"""Recomputes spelled-out equations (`62만 원 × 36개월 = 2,232만 원`) and reports
the ones that do not hold.
"""

from __future__ import annotations

import re

#: A quantity as documents write it: 2,400만 원, 380만원, 62만, 36개월, 3년, 0.707, 12%.
_NUMBER = (
    r"(\d[\d,]*(?:\.\d+)?)\s*(억|만|천)?\s*(?:원|개월|년|월|일|주|시간|명|석|회|건|대|개|%|배)?"
)
_OP = r"(\+|-|−|×|x|X|\*|÷|/)"
_EQ = r"(=|≈|≒|약)"
#: `A op B = C`, with either side allowed to carry a unit word.
_EQUATION = re.compile(rf"{_NUMBER}\s*{_OP}\s*{_NUMBER}\s*{_EQ}\s*{_NUMBER}")

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


def findings(text: str, *, where: str = "") -> list[dict[str, str]]:
    """Findings for every equation in `text` that does not hold within 1%.

    Scale words (만, 억) are applied before comparing, so both sides are
    compared in absolute terms.
    """
    out: list[dict[str, str]] = []
    for m in _EQUATION.finditer(text):
        a = _value(m.group(1), m.group(2))
        b = _value(m.group(4), m.group(5))
        c = _value(m.group(7), m.group(8))
        got = _compute(a, m.group(3), b)
        if got is None:
            continue
        # Unscaled operands beside a scaled answer (「2,400 + 1,140 = 3,540만 원」):
        # read the operands in the answer's scale.
        if m.group(8) and not m.group(2) and not m.group(5):
            got = got * _SCALE[m.group(8)]
        if abs(got - c) > max(abs(c), 1.0) * 0.01:
            out.append(
                {
                    "severity": "P0",
                    "rule": "arithmetic",
                    "where": where,
                    "message": (
                        f"「{m.group(0).strip()}」— 식이 맞지 않습니다. "
                        f"계산하면 {_pretty(got, m.group(8))}입니다."
                    ),
                }
            )
    return out


def _pretty(value: float, scale: str | None) -> str:
    """A number in the scale the document used: 3,540만 원, 1,308만 원, 3.2."""
    if scale in ("만", "억"):
        scaled = value / _SCALE[scale]
        text = f"{scaled:,.1f}".rstrip("0").rstrip(".")
        return f"{text}{scale} 원"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


__all__ = ["findings"]
