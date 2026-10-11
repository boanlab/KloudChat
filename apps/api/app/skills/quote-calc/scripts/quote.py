"""견적 항목(수량×단가, MM×월 단가)을 합쳐 공급가액(VAT 별도)·부가세·합계(VAT 포함)·예산 대비·시나리오를 원 단위로 계산합니다."""

from __future__ import annotations

import json
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

_DIGITS = ("", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_SMALL_UNITS = ("", "십", "백", "천")
_LARGE_UNITS = ("", "만", "억", "조", "경")
_ROUNDING = {"floor": ROUND_FLOOR, "half_up": ROUND_HALF_UP, "round": ROUND_HALF_UP,
             "ceil": ROUND_CEILING}


def hangul_amount(amount: int) -> str:
    """113560 → 「일십일만삼천오백육십」, as `doc_formats.hangul_amount` writes it."""
    if amount <= 0:
        return "영"
    groups: list[str] = []
    for index, unit in enumerate(_LARGE_UNITS):
        part = (amount // 10 ** (4 * index)) % 10_000
        if not part:
            continue
        words = "".join(
            _DIGITS[digit] + _SMALL_UNITS[place]
            for place in range(3, -1, -1)
            if (digit := (part // 10**place) % 10)
        )
        groups.append(words + unit)
    return "".join(reversed(groups))


def official_amount(amount: int) -> str:
    """「금113,560원(금일십일만삼천오백육십원)」."""
    return f"금{amount:,}원(금{hangul_amount(amount)}원)"


def number(value) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("숫자가 필요합니다")
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    text = str(value).strip().replace(",", "").replace(" ", "").removesuffix("원")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"숫자가 아닙니다: {value}") from exc


def won(value: Decimal, rounding: str = "half_up") -> int:
    return int(value.quantize(Decimal(1), rounding=_ROUNDING[rounding]))


def _line(item: dict) -> dict:
    name = str(item.get("name") or "항목")
    if "amount" in item and "unit_price" not in item:
        exact = number(item["amount"])
        qty_text, unit_price = "", None
    elif "mm" in item:
        qty = number(item["mm"])
        price = item.get("monthly_price", item.get("unit_price"))
        if price is None:
            raise ValueError(f"「{name}」 항목에 월 단가가 없습니다")
        unit_price = number(price)
        qty_text = f"{qty.normalize():f}"
        if item.get("people") is not None:
            # 「퍼블리셔 2명 각 4MM」: MM per person.
            people = number(item["people"])
            qty_text = f"{people.normalize():f} × {qty_text}"
            qty *= people
        exact = qty * unit_price
        item = {**item, "unit": item.get("unit") or "MM"}
    else:
        factors = item.get("qty", 1)
        factors = factors if isinstance(factors, list) else [factors]
        qty = Decimal(1)
        for factor in factors:
            qty *= number(factor)
        price = item.get("unit_price", item.get("monthly_price"))
        if price is None:
            raise ValueError(f"「{name}」 항목에 단가가 없습니다")
        unit_price = number(price)
        exact = qty * unit_price
        qty_text = " × ".join(f"{number(f).normalize():f}" for f in factors)
    amount = won(exact)
    row = {"name": name, "qty": qty_text, "unit": str(item.get("unit") or ""),
           "unit_price": int(unit_price) if unit_price is not None and unit_price == unit_price.to_integral_value() else (float(unit_price) if unit_price is not None else None),
           "amount": amount}
    if exact != amount:
        row["rounded_from"] = f"{exact:f}"
    if item.get("note"):
        row["note"] = str(item["note"])
    if item.get("excluded"):
        row["excluded"] = True
    return row


def _vat_of(supply: int, vat: dict | bool | None) -> int:
    if vat is False:
        return 0
    vat = vat if isinstance(vat, dict) else {}
    rate = number(vat.get("rate", 10))
    return won(Decimal(supply) * rate / 100, str(vat.get("rounding") or "floor"))


def _supply_from_incl(total: int, vat: dict | bool | None) -> int:
    if vat is False:
        return total
    rate = number((vat if isinstance(vat, dict) else {}).get("rate", 10))
    return won(Decimal(total) * 100 / (100 + rate))


def totals(items: list[dict], options: dict) -> dict:
    """Lines, subtotal, discount, 공급가액, 부가세, 합계(VAT 포함) for one set of items."""
    lines = [_line(item) for item in items]
    counted = [line for line in lines if not line.get("excluded")]
    subtotal = sum(line["amount"] for line in counted)
    vat = options.get("vat", {})
    basis = str(options.get("vat_basis") or "excl")
    discount = 0
    if isinstance(options.get("discount"), dict):
        spec = options["discount"]
        if "amount" in spec:
            discount = won(number(spec["amount"]))
        elif "rate" in spec:
            discount = won(Decimal(subtotal) * number(spec["rate"]) / 100)
    after = subtotal - discount
    if basis == "incl":
        total = after
        supply = _supply_from_incl(total, vat)
        tax = total - supply
    else:
        supply = after
        if options.get("truncate_to"):
            step = int(number(options["truncate_to"]))
            supply = supply // step * step
        tax = _vat_of(supply, vat)
        total = supply + tax
    out = {"lines": lines, "subtotal": subtotal, "discount": discount, "supply": supply,
           "vat": tax, "total": total, "total_official": official_amount(total),
           "supply_official": official_amount(supply), "vat_basis": basis}
    if budget := options.get("budget"):
        out["budget"] = against_budget(supply, total, budget, vat)
    if per_head := options.get("per_head"):
        n = number(per_head.get("n"))
        if n > 0:
            out["per_head"] = {"n": int(n), "excl": won(Decimal(supply) / n),
                               "incl": won(Decimal(total) / n)}
    if annual := options.get("annualize"):
        if annual.get("months"):
            months = number(annual["months"])
            out["annualized"] = {"months": int(months), "per_month": won(Decimal(total) / months),
                                 "per_year": won(Decimal(total) * 12 / months)}
        elif annual.get("years"):
            years = number(annual["years"])
            out["annualized"] = {"years": int(years), "per_year": won(Decimal(total) / years)}
    return out


def against_budget(supply: int, total: int, budget: dict, vat=None) -> dict:
    amount = won(number(budget.get("amount")))
    basis = str(budget.get("vat_basis") or "")
    if basis in ("excl", "incl"):
        compared = supply if basis == "excl" else total
        return {"amount": amount, "vat_basis": basis, "compared": compared,
                "headroom": amount - compared, "within": compared <= amount}
    return {"amount": amount, "vat_basis": "ambiguous",
            "if_excl": {"compared": supply, "headroom": amount - supply, "within": supply <= amount},
            "if_incl": {"compared": total, "headroom": amount - total, "within": total <= amount}}


def scenario(items: list[dict], spec: dict) -> list[dict]:
    by_name = {str(item.get("name")): dict(item) for item in items}
    order = [str(item.get("name")) for item in items]
    for change in spec.get("overrides") or []:
        name = str(change.get("name"))
        if name not in by_name:
            order.append(name)
            by_name[name] = {}
        by_name[name].update(change)
    for name in spec.get("remove") or []:
        by_name.pop(str(name), None)
    for item in spec.get("add") or []:
        order.append(str(item.get("name")))
        by_name[str(item.get("name"))] = dict(item)
    return [by_name[name] for name in dict.fromkeys(order) if name in by_name]


def quote(item: dict, vat=None) -> dict:
    amount = won(number(item.get("amount")))
    basis = str(item.get("vat_basis") or "")
    row = {"vendor": str(item.get("vendor") or item.get("name") or ""), "amount": amount,
           "vat_basis": basis or "ambiguous"}
    if basis == "incl":
        row.update(incl=amount, excl=_supply_from_incl(amount, vat))
    elif basis == "excl":
        row.update(excl=amount, incl=amount + _vat_of(amount, vat))
    for key in ("weeks", "note"):
        if key in item:
            row[key] = item[key]
    return row


def run(options: dict) -> dict:
    items = list(options.get("items") or [])
    result: dict = {"ok": True}
    if items:
        result.update(totals(items, options))
    scenarios = []
    for spec in options.get("scenarios") or []:
        merged = {**options, **{k: spec[k] for k in ("budget", "vat_basis", "discount") if k in spec}}
        done = totals(scenario(items, spec), merged)
        scenarios.append({"name": str(spec.get("name") or ""), "premise": str(spec.get("premise") or ""),
                          **{k: done[k] for k in ("subtotal", "supply", "vat", "total", "total_official")},
                          **({"budget": done["budget"]} if "budget" in done else {}),
                          **({"annualized": done["annualized"]} if "annualized" in done else {})})
    if scenarios:
        result["scenarios"] = scenarios
    if options.get("quotes"):
        rows = [quote(q, options.get("vat", {})) for q in options["quotes"]]
        if budget := options.get("budget"):
            amount, basis = won(number(budget.get("amount"))), str(budget.get("vat_basis") or "")
            for row in rows:
                if basis in ("excl", "incl") and basis in row:
                    row["within_budget"] = row[basis] <= amount
                    row["headroom"] = amount - row[basis]
        result["quotes"] = rows
    if not items and not result.get("quotes"):
        raise ValueError("items 또는 quotes 목록이 필요합니다")
    if items:
        result["table_markdown"] = table(result)
    return result


def table(result: dict) -> str:
    rows = ["| 항목 | 수량 | 단위 | 단가 | 금액 | 비고 |", "|---|---:|---|---:|---:|---|"]
    for line in result["lines"]:
        price = f"{line['unit_price']:,}" if isinstance(line.get("unit_price"), int) else (line.get("unit_price") or "")
        note = line.get("note", "") + (" (제외)" if line.get("excluded") else "")
        rows.append(f"| {line['name']} | {line['qty']} | {line['unit']} | {price} | {line['amount']:,} | {note} |")
    if result["discount"]:
        rows.append(f"| 할인 |  |  |  | -{result['discount']:,} |  |")
    # Every amount names its VAT basis; a bare 「총액」 reads as VAT included.
    rows.append(f"| 공급가액(VAT 별도) |  |  |  | {result['supply']:,} |  |")
    rows.append(f"| 부가세(10%) |  |  |  | {result['vat']:,} | 원 미만 절사 |")
    rows.append(f"| 합계(VAT 포함) |  |  |  | {result['total']:,} |  |")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    except (KeyError, ValueError, TypeError, ZeroDivisionError, ArithmeticError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if "table_markdown" in result:
        print(result["table_markdown"])
        print(f"합계(VAT 포함) {result['total_official']}")
        if budget := result.get("budget"):
            if budget["vat_basis"] == "ambiguous":
                print(f"예산 {budget['amount']:,}원: 기준 불분명 — 별도로 보면 여유 {budget['if_excl']['headroom']:,}원, "
                      f"포함으로 보면 여유 {budget['if_incl']['headroom']:,}원")
            else:
                print(f"예산 {budget['amount']:,}원({'부가세 별도' if budget['vat_basis'] == 'excl' else '부가세 포함'}) 대비 "
                      f"{'여유' if budget['within'] else '초과'} {abs(budget['headroom']):,}원")
    for row in result.get("scenarios") or []:
        print(f"[시나리오] {row['name']}({row['premise']}): 공급가액(VAT 별도) {row['supply']:,}원, "
              f"합계(VAT 포함) {row['total']:,}원")
    for row in result.get("quotes") or []:
        print(f"[견적] {row['vendor']}: 별도 {row.get('excl', '?'):,}원 / 포함 {row.get('incl', '?'):,}원"
              if "incl" in row else f"[견적] {row['vendor']}: {row['amount']:,}원(부가세 기준 불분명)")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
