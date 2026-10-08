"""TAM·SAM·SOM 을 하향식·상향식으로 계산하고, 단위·통화·연도를 맞추며, 가정·출처·민감도·정합성을 함께 냅니다.

인자(JSON 하나):
  {"market": "국내 중소기업 HR SaaS",
   "year": 2026,                         # 기준 연도: 금액은 이 해로 맞춥니다
   "unit": "억원",                        # 결과 단위: 원·만원·백만원·억원·조원, 또는 백만달러 등
   "fx": {"USD": {"rate": 1380, "source": "한국은행 ECOS 2026-09 평균 환율", "date": "2026-09-30"}},
   "max_age_years": 3,                   # 이보다 오래된 자료는 stale 로 표시
   "sensitivity": {"pct": 20},           # 가정·출처 미확인 값을 ±20% 흔든 범위
   "top_down": {
     "tam": [{"label": "국내 HR SaaS 시장", "value": 5200, "unit": "억원", "year": 2024,
              "source": "○○협회 2025 시장 보고서", "url": "https://...", "date": "2025-03-10"}],
     "growth": {"label": "연평균 성장률", "value": 8, "unit": "%", "source": "...", "date": "2025-03"},
     "sam": [{"label": "중소기업 비중", "value": 40, "unit": "%", "assumption": true,
              "basis": "통계청 KOSIS 기업규모별 종사자 비중 참고"}],
     "som": [{"label": "3년 내 점유율", "value": 3, "unit": "%", "assumption": true, "basis": "영업 인력 5명 기준"}]},
   "bottom_up": {
     "tam": [{"label": "대상 기업 수", "value": 12, "unit": "만개", "source": "통계청 KOSIS 전국사업체조사", "date": "2025-12"},
             {"label": "연 객단가", "value": 600, "unit": "만원", "assumption": true, "basis": "경쟁사 공개 요금 평균"}],
     "sam": [...], "som": {"direct": [{"label": "3년 차 고객 수", ...}, {"label": "연 객단가", ...}]}}}

- 각 단계는 곱하는 값의 목록입니다. sam·som 이 목록이면 앞 단계 값에 곱하고(비율), {"direct": [...]} 이면 그 값들만 곱해 따로 셉니다.
- 값마다 출처(source·url·date) 또는 "assumption": true 와 "basis"(근거)가 있어야 합니다. 둘 다 없으면 「출처 미확인」으로 표시합니다.
- 금액은 통화(원·달러·유로·엔·위안)와 자리 단위(천·만·백만·억·조, thousand·million·billion)를 읽어 결과 단위로 바꿉니다.
  다른 통화는 fx 에 환율(출처 포함)이 있어야 합니다. 자료 연도가 기준 연도와 다르면 growth 가 있을 때만 복리로 맞추고,
  없으면 맞추지 않고 year_mismatch 로 알립니다.
- 정합성: TAM ≥ SAM ≥ SOM, 비율 0~100%, 두 방식 TAM 차이(기본 2배 이상이면 알림).
결과는 표·계산식·가정 목록을 출력하고 마지막 줄에 결과 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
from fractions import Fraction

_KO_SCALES = {
    "": 1,
    "십": 10,
    "백": 100,
    "천": 10**3,
    "만": 10**4,
    "십만": 10**5,
    "백만": 10**6,
    "천만": 10**7,
    "억": 10**8,
    "십억": 10**9,
    "백억": 10**10,
    "천억": 10**11,
    "조": 10**12,
}
_EN_SCALES = {
    "thousand": 10**3,
    "k": 10**3,
    "million": 10**6,
    "mn": 10**6,
    "mil": 10**6,
    "m": 10**6,
    "billion": 10**9,
    "bn": 10**9,
    "b": 10**9,
    "trillion": 10**12,
    "tn": 10**12,
}
_CURRENCIES = {
    "원": "KRW",
    "krw": "KRW",
    "₩": "KRW",
    "달러": "USD",
    "불": "USD",
    "usd": "USD",
    "us$": "USD",
    "$": "USD",
    "유로": "EUR",
    "eur": "EUR",
    "€": "EUR",
    "엔": "JPY",
    "jpy": "JPY",
    "엔화": "JPY",
    "위안": "CNY",
    "cny": "CNY",
    "rmb": "CNY",
}
_CURRENCY_NAMES = {"KRW": "원", "USD": "달러", "EUR": "유로", "JPY": "엔", "CNY": "위안"}
_RATE_UNITS = {"%", "퍼센트", "프로"}
_STAGES = ("tam", "sam", "som")
_APPROACHES = {"top_down": "하향식", "bottom_up": "상향식"}


class SizingError(ValueError):
    pass


def frac(value, label: str = "값") -> Fraction:
    if isinstance(value, bool) or value is None:
        raise SizingError(f"{label}: 숫자가 아닙니다")
    if isinstance(value, (int, float)):
        return Fraction(str(value))
    text = str(value).replace(",", "").strip()
    try:
        return Fraction(text)
    except ValueError as exc:
        raise SizingError(f"{label}: 숫자가 아닙니다 ({value})") from exc


def parse_unit(unit: str | None, currency: str | None = None) -> dict:
    """「억원」→ money KRW ×10^8, 「USD million」→ money USD ×10^6, 「%」→ rate, 「만개」→ count ×10^4 「개」."""
    text = str(unit or "").strip()
    if text in _RATE_UNITS or text.lower() in ("percent", "pct"):
        return {"kind": "rate", "scale": Fraction(1, 100), "label": "%"}
    if text in ("배", "x", "×", "비율", "ratio"):
        return {"kind": "rate", "scale": Fraction(1), "label": "배"}
    low = text.lower().replace(" ", "")
    found = None
    for word in sorted(_CURRENCIES, key=len, reverse=True):
        if word in low:
            found = _CURRENCIES[word]
            low = low.replace(word, "", 1)
            break
    if found is None and currency:
        found = str(currency).upper()
        if found not in _CURRENCY_NAMES:
            raise SizingError(f"지원하지 않는 통화: {currency}")
    if found is not None:
        if low in _KO_SCALES:
            scale = _KO_SCALES[low]
        elif low in _EN_SCALES:
            scale = _EN_SCALES[low]
        else:
            raise SizingError(f"금액 단위를 읽을 수 없습니다: {unit}")
        return {
            "kind": "money",
            "currency": found,
            "scale": Fraction(scale),
            "label": text or found,
        }
    for prefix in sorted(_KO_SCALES, key=len, reverse=True):
        if prefix and text.startswith(prefix) and len(text) > len(prefix):
            return {
                "kind": "count",
                "scale": Fraction(_KO_SCALES[prefix]),
                "label": text[len(prefix) :],
            }
    return {"kind": "count", "scale": Fraction(1), "label": text}


def fmt(value: Fraction | float, decimals: int = 1) -> str:
    number = float(value)
    text = f"{number:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _year_of(text) -> int | None:
    found = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(found.group(0)) if found else None


def provenance(item: dict, year: int, max_age: int) -> dict:
    """출처 / 가정 / 출처 미확인, with the problems to report."""
    source = item.get("source")
    url = item.get("url")
    date = item.get("date")
    if isinstance(source, dict):
        url = url or source.get("url")
        date = date or source.get("date")
        source = source.get("name") or source.get("title")
    issues = []
    if item.get("assumption"):
        basis = str(item.get("basis") or "").strip()
        status = "가정"
        detail = basis or "근거 없음"
        if not basis:
            issues.append(
                {"kind": "assumption_without_basis", "why": "가정에 근거(basis)가 없습니다"}
            )
    elif source:
        status = "출처"
        detail = str(source).strip() + (f" ({url})" if url else "")
        if not date and not _year_of(source):
            issues.append({"kind": "source_without_date", "why": "출처에 날짜가 없습니다"})
    else:
        status = "출처 미확인"
        detail = "출처 미확인"
        issues.append({"kind": "unsourced", "why": "출처도 가정 표시도 없습니다"})
    data_year = (
        _year_of(date) or _year_of(item.get("year")) or (_year_of(source) if source else None)
    )
    if status == "출처" and data_year and year - data_year > max_age:
        issues.append(
            {
                "kind": "stale",
                "why": f"{data_year}년 자료로 기준 연도({year})보다 {year - data_year}년 오래됐습니다",
            }
        )
    return {"status": status, "detail": detail, "date": str(date or ""), "issues": issues}


class Sizer:
    def __init__(self, args: dict):
        self.market = str(args.get("market") or "").strip()
        self.year = int(args.get("year") or 0) or None
        target = parse_unit(args.get("unit") or "억원", args.get("currency"))
        if target["kind"] != "money":
            raise SizingError("결과 단위(unit)는 금액 단위여야 합니다")
        self.target = target
        self.decimals = int(args.get("decimals", 1))
        self.max_age = int(args.get("max_age_years", 3))
        sens = args.get("sensitivity") or {}
        self.pct = frac(sens.get("pct", 20), "sensitivity.pct") / 100
        self.gap_ratio = frac(args.get("gap_ratio", 2), "gap_ratio")
        self.fx: dict[str, dict] = {}
        for code, spec in (args.get("fx") or {}).items():
            spec = spec if isinstance(spec, dict) else {"rate": spec}
            self.fx[code.upper()] = spec
        self.assumptions: list[dict] = []
        self.issues: list[dict] = []
        self._seen: dict[str, int] = {}
        self._quiet = False

    # ── units ──
    def to_target_money(self, amount_base: Fraction, currency: str) -> tuple[Fraction, str]:
        """Money in base currency units → target unit, with the conversion note."""
        note = ""
        if currency != self.target["currency"]:
            pair = None
            if currency in self.fx and self.target["currency"] == "KRW":
                pair = frac(self.fx[currency].get("rate"), f"fx.{currency}")
                amount_base *= pair
                note = f"× 환율 {fmt(pair, 2)}원/{_CURRENCY_NAMES.get(currency, currency)}"
            elif self.target["currency"] in self.fx and currency == "KRW":
                pair = frac(self.fx[self.target["currency"]].get("rate"), "fx")
                amount_base /= pair
                note = f"÷ 환율 {fmt(pair, 2)}원/{_CURRENCY_NAMES.get(self.target['currency'])}"
            if pair is None:
                raise SizingError(f"{currency} → {self.target['currency']} 환율(fx)이 없습니다")
        return amount_base / self.target["scale"], note

    def register(self, item: dict, shown: str, stage: str, approach: str) -> dict:
        prov = (
            provenance(item, self.year or 0, self.max_age)
            if self.year
            else provenance(item, 9999, 10**6)
        )
        key = f"{item.get('label')}|{shown}"
        if key in self._seen:
            return self.assumptions[self._seen[key]]
        row = {
            "no": len(self.assumptions) + 1,
            "label": str(item.get("label") or "(이름 없음)"),
            "value": shown,
            "status": prov["status"],
            "basis": prov["detail"],
            "date": prov["date"],
            "where": f"{_APPROACHES[approach]} {stage.upper()}",
        }
        self._seen[key] = len(self.assumptions)
        self.assumptions.append(row)
        for issue in prov["issues"]:
            self.issues.append({**issue, "label": row["label"], "where": row["where"]})
        return row

    # ── one factor ──
    def factor(
        self, item: dict, stage: str, approach: str, override: Fraction | None = None
    ) -> dict:
        if not isinstance(item, dict) or "value" not in item:
            raise SizingError(
                f"{_APPROACHES[approach]} {stage.upper()}: 각 값은 label·value 가 있는 객체여야 합니다"
            )
        unit = parse_unit(item.get("unit"), item.get("currency"))
        raw = frac(item["value"], str(item.get("label") or "값"))
        value = raw if override is None else override
        label = str(item.get("label") or "값")
        shown = f"{fmt(raw, 4)}{str(item.get('unit') or '').strip() or unit['label']}"
        if unit["kind"] == "rate":
            if (
                unit["label"] == "%"
                and not (0 < raw <= 100)
                and stage != "growth"
                and not self._quiet
            ):
                self.issues.append(
                    {
                        "kind": "rate_out_of_range",
                        "label": label,
                        "why": f"비율 {fmt(raw, 4)}% 는 0~100% 밖입니다",
                        "where": f"{_APPROACHES[approach]} {stage.upper()}",
                    }
                )
            return {
                "kind": "rate",
                "mult": value * unit["scale"],
                "label": label,
                "shown": shown,
                "item": item,
            }
        if unit["kind"] == "money":
            base = value * unit["scale"]
            converted, note = self.to_target_money(base, unit["currency"])
            if note:
                fx_spec = (
                    self.fx.get(unit["currency"]) or self.fx.get(self.target["currency"]) or {}
                )
                self.register(
                    {"label": f"환율({unit['currency']})", **fx_spec, "value": fx_spec.get("rate")},
                    f"{fx_spec.get('rate')}",
                    stage,
                    approach,
                )
            return {
                "kind": "money",
                "mult": converted,
                "label": label,
                "shown": shown,
                "note": note,
                "year": _year_of(item.get("year")) or _year_of(item.get("date")),
                "item": item,
            }
        return {
            "kind": "count",
            "mult": value * unit["scale"],
            "label": label,
            "shown": shown,
            "item": item,
        }

    # ── one approach ──
    def approach(
        self, name: str, spec: dict, overrides: dict | None = None, quiet: bool = False
    ) -> dict:
        overrides = overrides or {}
        self._quiet = quiet
        out: dict = {"name": _APPROACHES[name], "formulas": []}
        growth = spec.get("growth")
        previous: Fraction | None = None
        for stage in _STAGES:
            entry = spec.get(stage)
            if entry is None:
                continue
            direct = isinstance(entry, dict)
            items = entry.get("direct") if direct else entry
            if not isinstance(items, list) or not items:
                raise SizingError(f"{_APPROACHES[name]} {stage.upper()}: 값 목록이 비어 있습니다")
            if stage == "tam" or direct:
                total = Fraction(1)
                parts = []
                money = 0
                years = []
                for item in items:
                    f = self.factor(item, stage, name, overrides.get(id(item)))
                    if not quiet:
                        self.register(item, f["shown"], stage, name)
                    total *= f["mult"]
                    if f["kind"] == "money":
                        money += 1
                        if f.get("year"):
                            years.append(f["year"])
                    parts.append(
                        f"{f['label']} {f['shown']}" + (f" {f['note']}" if f.get("note") else "")
                    )
                if money > 1:
                    raise SizingError(
                        f"{_APPROACHES[name]} {stage.upper()}: 금액끼리 곱했습니다 — 금액 값은 하나만"
                    )
                measure = "money" if money else "volume"
                text = " × ".join(parts)
                data_year = min(years) if years else None
                if measure == "money" and self.year and data_year and data_year != self.year:
                    if growth:
                        g = self.factor(growth, "growth", name, overrides.get(id(growth)))
                        if not quiet:
                            self.register(growth, g["shown"], "growth", name)
                        n = self.year - data_year
                        factor = (1 + g["mult"]) ** n
                        total *= factor
                        text += f" × 성장 보정 (1+{g['shown']})^{n}({data_year}→{self.year})"
                    elif not quiet:
                        self.issues.append(
                            {
                                "kind": "year_mismatch",
                                "label": stage.upper(),
                                "where": _APPROACHES[name],
                                "why": f"{data_year}년 값을 {self.year}년 기준으로 맞추지 않았습니다(growth 없음)",
                            }
                        )
                out[stage] = total
                out[f"{stage}_measure"] = measure
            else:
                if previous is None:
                    raise SizingError(
                        f"{_APPROACHES[name]} {stage.upper()}: 앞 단계가 없어 비율을 곱할 수 없습니다"
                    )
                total = previous
                parts = [
                    f"{stage_label(_STAGES[_STAGES.index(stage) - 1])} {self.show(previous, out)}"
                ]
                for item in items:
                    f = self.factor(item, stage, name, overrides.get(id(item)))
                    if f["kind"] != "rate" and not quiet:
                        self.issues.append(
                            {
                                "kind": "filter_not_rate",
                                "label": f["label"],
                                "where": f"{_APPROACHES[name]} {stage.upper()}",
                                "why": '비율이 아닌 값을 곱했습니다 — 직접 계산이면 {"direct": [...]}',
                            }
                        )
                    if not quiet:
                        self.register(item, f["shown"], stage, name)
                    total *= f["mult"]
                    parts.append(f"{f['label']} {f['shown']}")
                text = " × ".join(parts)
                out[stage] = total
                out[f"{stage}_measure"] = out.get(
                    f"{_STAGES[_STAGES.index(stage) - 1]}_measure", "money"
                )
            out["formulas"].append(
                f"{stage.upper()}({_APPROACHES[name]}) = {text} = {self.show(total, out, stage)}"
            )
            previous = total
        return out

    def show(self, value: Fraction, out: dict, stage: str | None = None) -> str:
        measure = out.get(f"{stage}_measure") if stage else None
        if measure is None:
            measure = "money"
        if measure == "volume":
            return fmt(value, self.decimals)
        return f"{fmt(value, self.decimals)}{self.target['label']}"


def stage_label(stage: str) -> str:
    return stage.upper()


def _factors(spec: dict) -> list[dict]:
    found = []
    for stage in (*_STAGES, "growth"):
        entry = spec.get(stage)
        if entry is None:
            continue
        if isinstance(entry, dict) and "direct" not in entry:
            found.append(entry)
            continue
        items = entry.get("direct") if isinstance(entry, dict) else entry
        found.extend(i for i in items or [] if isinstance(i, dict))
    return found


def _uncertain(item: dict) -> bool:
    return bool(item.get("assumption")) or not item.get("source") or "low" in item or "high" in item


def run(args: dict) -> dict:
    sizer = Sizer(args)
    approaches = {name: args[name] for name in _APPROACHES if isinstance(args.get(name), dict)}
    if not approaches:
        raise SizingError("top_down 또는 bottom_up 중 하나는 있어야 합니다")
    results = {name: sizer.approach(name, spec) for name, spec in approaches.items()}

    # consistency
    for name, res in results.items():
        values = [(s, res[s]) for s in _STAGES if s in res]
        for (a, va), (b, vb) in zip(values, values[1:], strict=False):
            same = res.get(f"{a}_measure") == res.get(f"{b}_measure")
            if same and vb > va:
                sizer.issues.append(
                    {
                        "kind": "order",
                        "where": _APPROACHES[name],
                        "label": f"{a.upper()}<{b.upper()}",
                        "why": f"{b.upper()}({fmt(vb)})가 {a.upper()}({fmt(va)})보다 큽니다 — TAM ≥ SAM ≥ SOM 이어야 합니다",
                    }
                )
            elif not same:
                sizer.issues.append(
                    {
                        "kind": "measure_mismatch",
                        "where": _APPROACHES[name],
                        "label": f"{a.upper()}/{b.upper()}",
                        "why": "금액과 수량을 비교할 수 없습니다",
                    }
                )
    if len(results) == 2:
        td, bu = results["top_down"].get("tam"), results["bottom_up"].get("tam")
        if td and bu and results["top_down"]["tam_measure"] == results["bottom_up"]["tam_measure"]:
            ratio = max(td, bu) / min(td, bu)
            if ratio >= sizer.gap_ratio:
                sizer.issues.append(
                    {
                        "kind": "approach_gap",
                        "where": "하향식·상향식",
                        "label": "TAM",
                        "why": f"두 방식 TAM 이 {fmt(ratio, 1)}배 차이 납니다 — 시장 정의나 가정을 다시 보세요",
                    }
                )

    # sensitivity: each uncertain factor ±pct (or its own low/high), then all together
    sensitivity = []
    for name, spec in approaches.items():
        uncertain = [i for i in _factors(spec) if _uncertain(i)]
        base = results[name]
        last = next(s for s in reversed(_STAGES) if s in base)

        def at(overrides, _name=name, _spec=spec):
            return sizer.approach(_name, _spec, overrides, quiet=True)

        lows, highs = {}, {}
        for item in uncertain:
            raw = frac(item["value"])
            low = frac(item["low"]) if "low" in item else raw * (1 - sizer.pct)
            high = frac(item["high"]) if "high" in item else raw * (1 + sizer.pct)
            if (
                str(item.get("unit") or "").strip() in _RATE_UNITS
                and "high" not in item
                and raw <= 100
            ):
                high = min(high, Fraction(100))
            lows[id(item)], highs[id(item)] = low, high
            r_low, r_high = at({id(item): low}), at({id(item): high})
            sizer._quiet = False
            sensitivity.append(
                {
                    "approach": _APPROACHES[name],
                    "label": str(item.get("label")),
                    "low": fmt(low, 4),
                    "high": fmt(high, 4),
                    "stage": last.upper(),
                    "result_low": fmt(min(r_low[last], r_high[last]), sizer.decimals),
                    "result_high": fmt(max(r_low[last], r_high[last]), sizer.decimals),
                }
            )
        if uncertain:
            r_low, r_high = at(lows), at(highs)
            sizer._quiet = False
            sensitivity.append(
                {
                    "approach": _APPROACHES[name],
                    "label": "불확실한 값 모두",
                    "low": "",
                    "high": "",
                    "stage": last.upper(),
                    "result_low": fmt(min(r_low[last], r_high[last]), sizer.decimals),
                    "result_high": fmt(max(r_low[last], r_high[last]), sizer.decimals),
                }
            )

    unit = sizer.target["label"]
    names = list(results)
    head = "| 구분 | " + " | ".join(_APPROACHES[n] for n in names) + " |"
    lines = [head, "|---|" + "---:|" * len(names)]
    for stage in _STAGES:
        if any(stage in results[n] for n in names):
            cells = [
                sizer.show(results[n][stage], results[n], stage) if stage in results[n] else "-"
                for n in names
            ]
            lines.append(f"| {stage.upper()} | " + " | ".join(cells) + " |")
    table = "\n".join(lines)
    a_lines = [
        "| 번호 | 항목 | 값 | 구분 | 근거·출처 | 날짜 | 쓰인 곳 |",
        "|---:|---|---:|---|---|---|---|",
    ]
    for row in sizer.assumptions:
        a_lines.append(
            f"| {row['no']} | {row['label']} | {row['value']} | {row['status']} | {row['basis']} | "
            f"{row['date'] or '-'} | {row['where']} |"
        )
    s_lines = []
    if sensitivity:
        s_lines = [
            f"| 방식 | 흔든 값 | 낮게 | 높게 | {sensitivity[0]['stage']} 범위({unit}) |",
            "|---|---|---:|---:|---|",
        ]
        for row in sensitivity:
            s_lines.append(
                f"| {row['approach']} | {row['label']} | {row['low'] or '-'} | {row['high'] or '-'} | "
                f"{row['result_low']} ~ {row['result_high']} |"
            )
    blocking = {
        "unsourced",
        "assumption_without_basis",
        "order",
        "rate_out_of_range",
        "measure_mismatch",
    }
    return {
        "ok": not any(i["kind"] in blocking for i in sizer.issues),
        "market": sizer.market,
        "year": sizer.year,
        "unit": unit,
        "results": {
            _APPROACHES[n]: {
                s: fmt(results[n][s], sizer.decimals) for s in _STAGES if s in results[n]
            }
            | {"measure": results[n].get("tam_measure", "money")}
            | (
                {"som_share_of_sam_pct": fmt(results[n]["som"] / results[n]["sam"] * 100, 2)}
                if results[n].get("sam")
                and "som" in results[n]
                and results[n].get("som_measure") == results[n].get("sam_measure")
                else {}
            )
            for n in names
        },
        "formulas": [f for n in names for f in results[n]["formulas"]],
        "assumptions": sizer.assumptions,
        "sensitivity": sensitivity,
        "issues": sizer.issues,
        "table_markdown": table,
        "assumptions_markdown": "\n".join(a_lines),
        "sensitivity_markdown": "\n".join(s_lines),
        "pct": fmt(sizer.pct * 100, 1),
    }


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    except (SizingError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(result["table_markdown"])
    print()
    for line in result["formulas"]:
        print(line)
    print()
    print(result["assumptions_markdown"])
    if result["sensitivity_markdown"]:
        print()
        print(f"민감도(±{result['pct']}%)")
        print(result["sensitivity_markdown"])
    for issue in result["issues"]:
        print(
            f"[{issue['kind']}] {issue.get('where', '')} {issue.get('label', '')}: {issue['why']}"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
