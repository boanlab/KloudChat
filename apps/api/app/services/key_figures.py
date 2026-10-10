"""Settled figures (판매가, 고정비, 목표 …) read once from the material and held fixed.

The writer is told them; a sentence giving one another value is flagged, a table row is reset.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.core import logs
from app.services import units

log = logging.getLogger(__name__)

MAX_FIGURES = 12

_PROMPT = """From the material below (the earlier conversation and the attachments), extract the key
figures that the whole document written from it must state with one and the same value.

Extract: named values — such as 판매가·단가·원가·변동비·고정비·마진·손익분기 판매량·목표 판매량·
고객 수·예산·투자액·기간·이탈률. Do not extract example or comparison values inside sentences.

Rules:
- Each value exactly as written in the material (unit included).
- At most {n}. Each item:
  {{"name": "박스당 판매가", "value": "20,000원", "aliases": ["판매가", "객단가"]}}
  aliases are other names the document may use for the same value (2–6 characters, in Korean).
- When the material gives one name different values (an old mail and a new one, a brochure and
  a measurement), do not settle that name; list each value separately with its source and time:
  {{"name": "1TB 복구 시간", "values": [{{"value": "2시간", "where": "제품 소개서", "when": ""}},
  {{"value": "4.5시간", "where": "독립 측정", "when": "2026-09"}}]}}

Material:
{material}

Answer with one JSON array only."""

#: A figure as written: 「20,000원」, 「3,000만 원」, 「2,500박스」, 「8%」, 「6개월」.
_VALUE = re.compile(
    rf"(?P<amount>{units._TERM})\s?(?P<unit>원|박스|명|가구|개월|회|건|개|%|세트)"
)
#: A sentence about a case other than the settled one: a scenario, a change, a range.
_CASE = re.compile(
    r"시나리오|민감도|가정하면|라면|만약|인상|인하|올리|내리|늘리|줄이|확장|변경|바뀌|증가|감소|"
    r"상승|하락|최대|최소|전제|이상|이하|초과|미만|~|±|대비|차이|할인|프로모션|경쟁사|타사|업계|평균|"
    r"\d{1,2}\s?개월\s?차|\d{1,2}\s?월에|(?:되|하|지|이|으|라)면(?=[\s,])|경우|때는|때에는"
)


@dataclass(slots=True)
class Figure:
    name: str
    value: str
    aliases: list[str] = field(default_factory=list)

    @property
    def unit(self) -> str:
        m = _VALUE.search(self.value)
        return m.group("unit") if m else ""

    @property
    def number(self) -> float | None:
        m = _VALUE.search(self.value)
        try:
            return units._ko_number(m.group("amount")) if m else None
        except ValueError:
            return None

    @property
    def names(self) -> list[str]:
        return [n for n in dict.fromkeys([self.name, *self.aliases]) if len(n) >= 2]


def _values(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _VALUE.finditer(text or ""):
        try:
            out.append((m.group("unit"), units._ko_number(m.group("amount"))))
        except ValueError:
            continue
    return out


def _given(figure: Figure, material: str) -> bool:
    """The value is the material's: the same number and unit appear in it."""
    number, unit = figure.number, figure.unit
    if number is None or not unit:
        return False
    return any(
        u == unit and abs(v - number) < 1e-6 * max(1.0, number) for u, v in _values(material)
    )


def _contested(figure: Figure, material: str) -> bool:
    """The material calls the figure by its name with another value too."""
    for name in figure.names:
        for value in _stated_after(name, material, figure.unit):
            if figure.number is not None and abs(value - figure.number) > 1e-6 * max(1.0, value):
                return True
    return False


def _stated_after(name: str, text: str, unit: str) -> list[float]:
    """Values in `unit` written right after `name`: 「판매가는 20,000원」."""
    # The name as a whole word, and the value not an operand of a formula.
    pattern = re.compile(
        rf"(?<![가-힣A-Za-z]){re.escape(name)}\s?(?:은|는|이|가|을|를|:|=|\|)?\s?(?:\|\s?)?"
        r"(?:약\s?|월\s?|연\s?|박스당\s?)?"
        rf"(?P<amount>{units._TERM})\s?{re.escape(unit)}(?!\s?[-−+×x*÷/(])"
    )
    found = []
    for m in pattern.finditer(text or ""):
        try:
            found.append(units._ko_number(m.group("amount")))
        except ValueError:
            continue
    return found


@dataclass(slots=True)
class Conflict:
    """One named figure the material gives two or more values for, each with where it came from.

    Kept, never settled: the writer is shown every value, and no check rewrites one into another."""

    name: str
    #: `{"value", "where", "when", "line"}` per value, in the material's order.
    values: list[dict] = field(default_factory=list)

    def numbers(self) -> list[tuple[str, float]]:
        out = []
        for value in self.values:
            out.extend(_loose(str(value.get("value") or "")))
        return out


MAX_CONFLICTS = 8


def _line_with(material: str, name: str, value: str) -> str:
    """The material line that gives `value` (by number and unit), the one naming `name` first."""
    wanted = _loose(value)
    lines = [line.strip() for line in (material or "").splitlines() if line.strip()]
    holding = [line for line in lines
               if wanted and any(u == wanted[0][0] and abs(v - wanted[0][1]) < 1e-6 * max(1.0, v)
                                 for u, v in _loose(line))]
    named = [line for line in holding if name and name in line]
    return ((named or holding or [""])[0])[:240]


_DATE_IN_LINE = re.compile(
    r"20\d{2}[-./년]\s?\d{1,2}(?:[-./월]\s?\d{1,2})?|(?<!\d)\d{1,2}/\d{1,2}(?!\d)"
)


#: A conflicting value may be a time, a size or a score, not only what is settled.
_LOOSE = re.compile(
    rf"(?P<amount>{units._TERM})\s?(?P<unit>원|박스|명|가구|개월|회|건|개|%|세트|시간|분|초|일|주|년|"
    r"TB|GB|MB|km|kWh|kg|점|배|석|대|곳)"
)


def _loose(text: str) -> list[tuple[str, float]]:
    out = []
    for m in _LOOSE.finditer(text or ""):
        try:
            out.append((m.group("unit"), units._ko_number(m.group("amount"))))
        except ValueError:
            continue
    return out


def _conflict(name: str, values: list[dict], material: str) -> Conflict | None:
    kept: list[dict] = []
    seen: list[object] = []
    flat = re.sub(r"\s+", "", material or "")
    given = _loose(material)
    for row in values:
        value = str(row.get("value") or "").strip()[:40]
        if not value:
            continue
        stated = _loose(value)
        if stated:
            unit, number = stated[0]
            if not any(u == unit and abs(v - number) < 1e-6 * max(1.0, number) for u, v in given):
                continue
            key: object = (unit, number)
        elif re.sub(r"\s+", "", value) in flat:
            key = re.sub(r"\s+", "", value)  # a word value (a region, a plan name), verbatim
        else:
            continue
        if key in seen:
            continue
        seen.append(key)
        line = _line_with(material, name, value) or next(
            (ln.strip()[:240] for ln in material.splitlines() if value in ln), "")
        when = str(row.get("when") or "").strip()[:30]
        if not when and (stamp := _DATE_IN_LINE.search(line)):
            when = stamp.group(0)
        kept.append({"value": value, "where": str(row.get("where") or "").strip()[:60],
                     "when": when, "line": line})
    return Conflict(name[:30], kept) if len(kept) >= 2 else None


def parse_all(text: str, material: str) -> tuple[list[Figure], list[Conflict]]:
    """The model's list, kept to what the material gives; a contested name becomes a conflict."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in (text or "") else "[]"
    try:
        rows = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return [], []
    figures: list[Figure] = []
    conflicts: list[Conflict] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, value = str(row.get("name") or "").strip(), str(row.get("value") or "").strip()
        if not name or name in seen:
            continue
        if isinstance(row.get("values"), list):
            listed = [v if isinstance(v, dict) else {"value": v} for v in row["values"]]
            if found := _conflict(name, listed, material):
                seen.add(name)
                conflicts.append(found)
            continue
        aliases = [
            str(a).strip() for a in (row.get("aliases") or []) if 2 <= len(str(a).strip()) <= 8
        ]
        figure = Figure(name[:30], value[:30], aliases[:4])
        if not _given(figure, material):
            continue
        seen.add(name)
        if _contested(figure, material):
            # Two values under one name: both kept as a conflict, neither settled.
            others = [
                {"value": units._ko_written(v, "") + figure.unit}
                for n in figure.names for v in _stated_after(n, material, figure.unit)
            ]
            if found := _conflict(name, [{"value": figure.value}, *others], material):
                conflicts.append(found)
            continue
        figures.append(figure)
    return figures[:MAX_FIGURES], conflicts[:MAX_CONFLICTS]


def parse(text: str, material: str) -> list[Figure]:
    """The model's list, kept to what the material gives and does not contest."""
    return parse_all(text, material)[0]


#: How a document treats two values for one thing when its 서식 says nothing
#: (`conflict_rule` in its 진행 과정 설정, `services/process_settings.py`).
DEFAULT_RULE = ("State in the sentence which value you used and why. Do not drop the other "
                "value or merge them into one.")


def conflict_block(conflicts: list[Conflict], rule: str = "") -> str:
    """The values the material disagrees on, as the writer is shown them, with the 서식's rule
    for them (`conflict_rule`)."""
    if not conflicts:
        return ""
    lines = []
    for c in conflicts:
        shown = " / ".join(
            v["value"] + (f"({', '.join(x for x in (v.get('where'), v.get('when')) if x)})"
                          if v.get("where") or v.get("when") else "")
            + (f" — 「{v['line'][:100]}」" if v.get("line") else "")
            for v in c.values
        )
        lines.append(f"- {c.name}: {shown}")
    rule = rule or DEFAULT_RULE
    return (
        "# 자료끼리 다른 값\nThe material gives these things different values. They are not "
        "settled figures (확정 수치). "
        + rule + "\n" + "\n".join(lines)
    )


def explains(sentences: list[str], conflicts: list[Conflict]) -> bool:
    """Whether two places giving one metric two values are a conflict the material itself has."""
    for c in conflicts:
        numbers = c.numbers()
        if len(numbers) < 2:
            continue
        if all(any(any(u == cu and abs(v - cv) < 1e-6 * max(1.0, cv) for cu, cv in numbers)
                   for u, v in _loose(s)) for s in sentences):
            return True
    return False


def block(figures: list[Figure]) -> str:
    """The figures as the writer is told them."""
    if not figures:
        return ""
    lines = [f"- {f.name}: {f.value}" for f in figures]
    return (
        "# 확정 수치\nValues settled in the earlier conversation and the material. In every "
        "section, table and figure of this document, use only the values below for these names; do "
        "not recalculate them or change them to other values. Figures calculated from these values "
        "must be calculated correctly. Use a different value only when separately discussing "
        "another case, such as a price increase, and then say in the sentence that it is that "
        "case.\n"
        + "\n".join(lines)
    )


#: A kpi block's line: a value, then the label it stands for.
_KPI_LINE = re.compile(r"(?P<value>\d[\d,.]{0,15}\s?(?:조|억|만)?\s?(?:원|박스|명|가구|%))\s?\|\s?"
                       r"(?P<label>[^|\d]{2,30})")
#: A count a per-unit amount could be multiplied by: 「2,000박스」, 「1만 명」.
_QUANTITY = re.compile(r"\d[\d,]{0,15}\s?(?:만\s?)?(?:박스|개|명|가구|건|세트|회)(?![가-힣]?당)")


def _power_of_ten(ratio: float) -> bool:
    return any(abs(ratio - 10**k) < 1e-6 * 10**k or abs(ratio - 10**-k) < 1e-6 * 10**-k
               for k in range(3, 9))


def conflicts(text: str, figures: list[Figure]) -> list[tuple[str, Figure, float]]:
    """`(sentence or row, figure, value written)` where `text` gives a settled figure another value.

    Sentences about another case (a scenario, a change) are skipped."""
    found: list[tuple[str, Figure, float]] = []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if kpi := _KPI_LINE.fullmatch(stripped):
            # Value before label: 「50만 원 | 손익분기 매출」.
            stripped = f"{kpi.group('label')} {kpi.group('value')}"
        if not stripped or stripped.startswith(("!", "```", "#")):
            continue
        if stripped.startswith("|"):
            # Only a row labelled with the figure's name: 「| 판매가 | 2,000원 |」.
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            label = re.sub(r"\*|\s", "", cells[0]) if cells else ""
            for figure in figures:
                if (len(cells) >= 2 and figure.number is not None
                        and label in {re.sub(r"\s", "", n) for n in figure.names}):
                    wrote = [v for u, v in _values(cells[1]) if u == figure.unit]
                    if wrote and abs(wrote[0] - figure.number) > 1e-6 * max(1.0, figure.number):
                        found.append((stripped, figure, wrote[0]))
            continue
        for piece in units.SENTENCE_BREAK.split(stripped):
            if _CASE.search(piece):
                continue
            for figure in figures:
                if figure.number is None:
                    continue
                for name in figure.names:
                    wrote = [v for v in _stated_after(name, piece, figure.unit)
                             if abs(v - figure.number) > 1e-6 * max(1.0, figure.number)]
                    if name != figure.name:
                        # An alias a thousandfold off is a total, not the per-unit figure,
                        # unless no quantity in the sentence could multiply it.
                        wrote = [v for v in wrote
                                 if 1e-3 < v / max(figure.number, 1e-9) < 1e3
                                 or (_power_of_ten(v / max(figure.number, 1e-9))
                                     and not _QUANTITY.search(piece))]
                    if wrote:
                        found.append((piece, figure, wrote[0]))
                        break
    return found


def mend_rows(text: str, figures: list[Figure]) -> str:
    """A table row named exactly for a settled figure (「| 판매가 | 2,000원 |」) gets its value."""
    lines = (text or "").split("\n")
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = re.sub(r"\*|\s", "", cells[0])
        for figure in figures:
            if label not in {re.sub(r"\s", "", n) for n in figure.names}:
                continue
            values = _values(cells[1])
            if (len(values) == 1 and values[0][0] == figure.unit and figure.number is not None
                    and abs(values[0][1] - figure.number) > 1e-6 * max(1.0, figure.number)):
                log.info(
                    "row %r set to the settled %s", logs.safe(cells[0]), logs.safe(figure.value)
                )
                cells[1] = figure.value
                lines[i] = "| " + " | ".join(cells) + " |"
            break
    return "\n".join(lines)


#: Material worth settling: an amount in won, boxes, people, households or percent.
HAS_VALUES = re.compile(r"\d[\d,]{0,20}\s?(?:만|억|조)?\s?(?:원|박스|명|가구|%)")


async def settle_all(
    material: str, complete, model: str, api_key: str
) -> tuple[list[Figure], list[Conflict], dict]:
    """The named figures `material` settles and the ones it gives two values for.

    `complete` is `(model, messages, api_key, max_tokens) -> (text, usage)`."""
    if not material or not HAS_VALUES.search(material):
        return [], [], {"inputTokens": 0, "outputTokens": 0}
    text, spent = await complete(
        model,
        [{"role": "user", "content": _PROMPT.format(n=MAX_FIGURES, material=material[:12000])}],
        api_key,
        1200,
    )
    figures, conflicts = parse_all(text, material)
    if conflicts:
        log.info("conflicting figures kept: %s", logs.safe([c.name for c in conflicts]))
    return figures, conflicts, spent


async def settle(material: str, complete, model: str, api_key: str) -> tuple[list[Figure], dict]:
    """The named figures `material` settles, asked of the model once."""
    figures, _, spent = await settle_all(material, complete, model, api_key)
    return figures, spent


#: An amount that is an operand: next to 「×」 or 「÷」 — 「3,000박스 × 2,000원」.
_OPERAND_NEXT_TO = re.compile(
    rf"(?<=[×x*÷])(?P<lead>\s?(?:[가-힣]{{1,8}}\s)?)(?P<a>{units._TERM})\s?(?P<au>원|박스)"
    rf"|(?P<b>{units._TERM})\s?(?P<bu>원|박스)(?=\s?[×x*÷])"
)


def mend_operands(text: str, figures: list[Figure]) -> str:
    """Operands of a written product that are a settled figure a power of ten off, reset.

    The product itself is recomputed afterwards by the arithmetic mends."""
    if not text or not figures or not re.search(r"[×x*÷]", text):
        return text

    def mend(m: re.Match) -> str:
        lead = m.group("lead") or ""
        if m.group("a"):
            amount, unit = m.group("a"), m.group("au")
        else:
            amount, unit = m.group("b"), m.group("bu")
        try:
            value = units._ko_number(amount)
        except ValueError:
            return m.group(0)
        # Only an operand named as the figure in the preceding words; an unnamed amount
        # may be another quantity altogether.
        before = m.string[max(0, m.start() - 40):m.start()] + lead
        for figure in figures:
            if figure.unit != unit or figure.number is None or not value:
                continue
            if not any(name in before for name in figure.names):
                continue
            ratio = value / figure.number
            if any(abs(ratio - 10**k) < 1e-9 * 10**k for k in (-4, -3, -2, -1, 1, 2, 3, 4)):
                log.info("operand %s%s set to the settled %s", amount, unit, figure.value)
                return lead + figure.value
        return m.group(0)

    return _OPERAND_NEXT_TO.sub(mend, text)


#: The parts of a unit-economics plan, by what the material calls them.
_ROLES = {
    "price": re.compile(r"판매가|객단가|가격|단가"),
    "cost": re.compile(r"변동비|원가"),
    "fixed": re.compile(r"고정비"),
    "margin": re.compile(r"공헌이익|마진|기여이익|한계이익"),
    "target": re.compile(r"목표"),
}


def _role(figure: Figure, role: str) -> bool:
    if not _ROLES[role].search(figure.name):
        return False
    if role == "target":
        return figure.unit == "박스"
    return figure.unit == "원"


def _won(value: float) -> str:
    """「12,000원」, 「5,000만원」: a won amount as the documents write it."""
    return units._ko_written(value, "만" if value >= 100_000 else "") + "원"


def derived(figures: list[Figure]) -> list[Figure]:
    """Figures computed from the settled ones: margin, break-even, target profit and revenue."""
    pick = {role: next((f for f in figures if _role(f, role)), None) for role in _ROLES}
    price, cost, fixed = pick["price"], pick["cost"], pick["fixed"]
    margin_given, target = pick["margin"], pick["target"]
    have = {f.name for f in figures}
    out: list[Figure] = []
    margin = None
    if price and cost and price.number and cost.number is not None:
        margin = price.number - cost.number
        if margin_given and margin_given.number and abs(margin_given.number - margin) > 0.5:
            return []  # the material's own margin disagrees: nothing is derived
    elif margin_given and margin_given.number:
        margin = margin_given.number
    if margin is None or margin <= 0:
        return []
    if not margin_given:
        out.append(Figure("박스당 공헌이익", _won(margin), ["공헌이익", "마진"]))
    if fixed and fixed.number:
        bep = -(-fixed.number // margin)
        if not any("손익분기" in n for n in have):
            out.append(Figure("손익분기 판매량", f"{bep:,.0f}박스", ["손익분기점", "손익분기"]))
        if price and price.number:
            out.append(Figure("손익분기 매출", _won(bep * price.number), []))
        if target and target.number:
            profit = target.number * margin - fixed.number
            out.append(Figure("목표 판매량 시 월 영업이익", _won(profit), ["목표 영업이익"]))
    if target and target.number and price and price.number:
        out.append(Figure("목표 판매량 시 월 매출", _won(target.number * price.number), []))
    return out
