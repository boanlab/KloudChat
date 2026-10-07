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

_PROMPT = """아래 자료(앞선 대화와 첨부)에서, 이것으로 쓸 문서 전체가 같은 값으로 써야 할 핵심
수치를 뽑아라.

뽑을 것: 이름이 붙은 값 — 판매가·단가·원가·변동비·고정비·마진·손익분기 판매량·목표 판매량·
고객 수·예산·투자액·기간·이탈률 같은 것. 문장 속 예시나 비교용 값은 뽑지 않는다.

규칙:
- 값은 자료에 적힌 그대로(단위 포함).
- 자료가 같은 이름에 서로 다른 값을 주면 그 이름은 뺀다.
- 최대 {n}개. 각 항목:
  {{"name": "박스당 판매가", "value": "20,000원", "aliases": ["판매가", "객단가"]}}
  aliases는 문서에서 같은 값을 부를 다른 이름(2~6자).

자료:
{material}

JSON 배열 하나만 답하라."""

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


def parse(text: str, material: str) -> list[Figure]:
    """The model's list, kept to what the material gives and does not contest."""
    block = text[text.find("[") : text.rfind("]") + 1] if "[" in (text or "") else "[]"
    try:
        rows = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return []
    figures: list[Figure] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, value = str(row.get("name") or "").strip(), str(row.get("value") or "").strip()
        aliases = [
            str(a).strip() for a in (row.get("aliases") or []) if 2 <= len(str(a).strip()) <= 8
        ]
        figure = Figure(name[:30], value[:30], aliases[:4])
        if not name or name in seen or not _given(figure, material) or _contested(figure, material):
            continue
        seen.add(name)
        figures.append(figure)
    return figures[:MAX_FIGURES]


def block(figures: list[Figure]) -> str:
    """The figures as the writer is told them."""
    if not figures:
        return ""
    lines = [f"- {f.name}: {f.value}" for f in figures]
    return (
        "# 확정 수치\n앞선 대화와 자료에서 정해진 값이다. 이 문서의 모든 절·표·그림에서 이 이름의 "
        "값은 아래 것만 쓰고, 다시 계산하거나 다른 값으로 바꾸지 마라. 이 값에서 계산한 수치는 "
        "계산이 맞아야 한다. 가격 인상 같은 다른 경우를 따로 말할 때만 다른 값을 쓰고, 그때는 "
        "그 경우임을 문장에 밝혀라.\n" + "\n".join(lines)
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
        for piece in re.split(r"(?<=[.!?])\s+", stripped):
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


async def settle(material: str, complete, model: str, api_key: str) -> tuple[list[Figure], dict]:
    """The named figures `material` settles, asked of the model once.

    `complete` is `(model, messages, api_key, max_tokens) -> (text, usage)`."""
    if not material or not HAS_VALUES.search(material):
        return [], {"inputTokens": 0, "outputTokens": 0}
    text, spent = await complete(
        model,
        [{"role": "user", "content": _PROMPT.format(n=MAX_FIGURES, material=material[:12000])}],
        api_key,
        900,
    )
    return parse(text, material), spent


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
