"""계산식·단위 변환·증감률을 정확히 다시 계산해 적어 둔 값과 대조합니다.

인자(JSON 하나): {"items": [
  {"label": "부가세", "expr": "1250000 * 0.1", "expected": 125000},
  {"label": "매출 증가율", "kind": "change", "old": 120, "new": 150, "expected": 25},
  {"label": "점유율 변화", "kind": "change", "old": 12.5, "new": 14, "as": "pp", "expected": 1.5},
  {"label": "비중", "kind": "share", "part": 30, "whole": 120, "expected": 25},
  {"label": "단위", "kind": "convert", "value": 1.2, "from": "억원", "to": "천원", "expected": 120000}
]}
expected 는 생략할 수 있고(계산만), "tolerance"(절대 오차)나 "decimals"(반올림 자리)로 비교 기준을 정합니다.
결과는 표로 출력하고 마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import sys
from fractions import Fraction

UNITS: dict[str, tuple[str, Fraction]] = {}
for _dimension, _table in {
    "money": {"원": 1, "천원": 10**3, "만원": 10**4, "백만원": 10**6, "천만원": 10**7,
              "억원": 10**8, "조원": 10**12, "천": 10**3, "만": 10**4, "백만": 10**6,
              "억": 10**8, "조": 10**12},
    "length": {"mm": Fraction(1, 1000), "cm": Fraction(1, 100), "m": 1, "km": 1000},
    "mass": {"mg": Fraction(1, 10**6), "g": Fraction(1, 1000), "kg": 1, "t": 1000, "톤": 1000},
    "time": {"초": 1, "s": 1, "분": 60, "min": 60, "시간": 3600, "h": 3600, "일": 86400},
    "area": {"m2": 1, "㎡": 1, "평": Fraction(400, 121), "ha": 10**4, "km2": 10**6},
    "ratio": {"%": Fraction(1, 100), "‰": Fraction(1, 1000), "배": 1, "비율": 1},
}.items():
    for _name, _factor in _table.items():
        UNITS[_name] = (_dimension, Fraction(_factor))

BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}


def round_half_up(value, places: int = 0) -> Fraction:
    """Rounding as people do it (2.5 → 3), not Python's half-to-even."""
    scale = Fraction(10) ** int(places)
    scaled = Fraction(value) * scale
    whole = math.floor(abs(scaled) + Fraction(1, 2))
    return Fraction(whole if scaled >= 0 else -whole) / scale


FUNCTIONS = {
    "round": round_half_up, "abs": abs, "min": min, "max": max,
    "sqrt": lambda x: math.sqrt(float(x)), "sum": lambda *xs: sum(xs),
    "avg": lambda *xs: sum(xs) / len(xs),
}


def number(value) -> Fraction | float:
    if isinstance(value, bool) or value is None:
        raise ValueError("숫자가 필요합니다")
    if isinstance(value, (int, Fraction)):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    text = str(value).strip().replace(",", "").replace(" ", "")
    if text.endswith("%"):
        return Fraction(text[:-1])
    return Fraction(text)


def evaluate(expression: str):
    if len(expression) > 500:
        raise ValueError("식이 너무 깁니다")
    tree = ast.parse(expression.replace(",", "").replace("×", "*").replace("÷", "/"), mode="eval")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return number(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = walk(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ZeroDivisionError("0으로 나눌 수 없습니다")
            return BINARY[type(node.op)](left, right)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            left, right = walk(node.left), walk(node.right)
            if abs(right) > 100:
                raise ValueError("지수가 너무 큽니다")
            if isinstance(right, Fraction) and right.denominator == 1 and isinstance(left, Fraction):
                return left ** int(right)
            return float(left) ** float(right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
            result = FUNCTIONS[node.func.id](*(walk(arg) for arg in node.args))
            return number(result) if isinstance(result, (int, float)) else result
        raise ValueError(f"지원하지 않는 식입니다: {ast.unparse(node)[:40]}")

    return walk(tree)


def convert(value, source: str, target: str):
    if source not in UNITS or target not in UNITS:
        raise ValueError(f"모르는 단위입니다: {source if source not in UNITS else target}")
    (dim_a, factor_a), (dim_b, factor_b) = UNITS[source], UNITS[target]
    if dim_a != dim_b:
        raise ValueError(f"{source} 와 {target} 는 서로 바꿀 수 없는 단위입니다")
    return number(value) * factor_a / factor_b


def decimals_of(value) -> int:
    text = str(value).replace(",", "")
    return len(text.split(".", 1)[1]) if "." in text else 0


def compute(item: dict) -> tuple[object, str]:
    kind = item.get("kind") or ("expr" if "expr" in item else "")
    if kind == "expr":
        return evaluate(str(item["expr"])), str(item["expr"])
    if kind == "change":
        old, new = number(item["old"]), number(item["new"])
        if item.get("as") == "pp":
            return new - old, f"{fmt(new)} - {fmt(old)} (%p)"
        if old == 0:
            raise ZeroDivisionError("기준값이 0이라 증감률을 낼 수 없습니다")
        return (new - old) / old * 100, f"({fmt(new)} - {fmt(old)}) / {fmt(old)} × 100"
    if kind == "share":
        part, whole = number(item["part"]), number(item["whole"])
        if whole == 0:
            raise ZeroDivisionError("전체가 0이라 비중을 낼 수 없습니다")
        return part / whole * 100, f"{fmt(part)} / {fmt(whole)} × 100"
    if kind == "convert":
        value = convert(item["value"], str(item["from"]), str(item["to"]))
        return value, f"{fmt(number(item['value']))}{item['from']} → {item['to']}"
    raise ValueError("kind 는 expr, change, share, convert 중 하나여야 합니다")


def fmt(value) -> str:
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return f"{value.numerator:,}"
        value = float(value)
    if isinstance(value, float):
        return f"{value:,.10f}".rstrip("0").rstrip(".")
    return str(value)


def judge(item: dict, value) -> tuple[str, str]:
    if "expected" not in item or item["expected"] in (None, ""):
        return "computed", ""
    expected = number(item["expected"])
    actual = Fraction(value) if not isinstance(value, Fraction) else value
    gap = abs(actual - expected)
    if "tolerance" in item:
        ok = gap <= number(item["tolerance"])
        return ("match" if ok else "mismatch"), f"오차 {fmt(gap)}"
    if gap <= abs(expected) * Fraction(1, 10**12):
        return "match", ""
    places = int(item["decimals"]) if "decimals" in item else decimals_of(item["expected"])
    rounded = round_half_up(actual, places)
    if rounded == expected:
        return "rounded", f"소수 {places}자리 반올림 시 일치"
    return "mismatch", f"차이 {fmt(actual - expected)}"


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    items = options.get("items") if isinstance(options, dict) else None
    if not isinstance(items, list) or not items:
        print(json.dumps({"ok": False, "error": "items 목록이 필요합니다"}, ensure_ascii=False))
        return 1
    results = []
    for index, item in enumerate(items[:200], start=1):
        label = str(item.get("label") or f"항목 {index}")
        try:
            value, formula = compute(item)
            status, note = judge(item, value)
            row = {
                "label": label,
                "formula": formula,
                "value": fmt(value),
                "value_number": float(value),
                "status": status,
            }
            if "expected" in item:
                row["expected"] = item["expected"]
            if note:
                row["note"] = note
        except (ValueError, ZeroDivisionError, KeyError, TypeError, SyntaxError, ArithmeticError) as exc:
            row = {"label": label, "status": "error", "note": str(exc) or type(exc).__name__}
        results.append(row)
    for row in results:
        mark = {"match": "일치", "rounded": "반올림 일치", "mismatch": "불일치", "computed": "계산",
                "error": "오류"}[row["status"]]
        detail = f"{row.get('formula', '')} = {row.get('value', '')}".strip(" =")
        expected = f" (적힌 값 {row['expected']})" if "expected" in row else ""
        note = f" — {row['note']}" if row.get("note") else ""
        print(f"[{mark}] {row['label']}: {detail}{expected}{note}")
    summary = {
        "ok": True,
        "checked": len(results),
        "mismatches": sum(r["status"] == "mismatch" for r in results),
        "errors": sum(r["status"] == "error" for r in results),
        "items": results,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
