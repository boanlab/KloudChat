"""Guard repairs settled: supported values kept, unsupported ones marked, nothing emptied.

A post-writing guard (grounding trace, skill post-check scripts, copy check, a rewrite) may
blank a value as 「[수치]」 or drop a sentence. `settle` compares the text before and after
the guard: a blanked value the evidence supports (or that is an ordinal, a count of the
answer's own items, a character count, a year the request names) goes back as written; any
other blanked value goes back with 「(확인 필요)」 after it. A paragraph the guard emptied and a
table left with only its header are restored, marked when the evidence does not cover them.
"""

from __future__ import annotations

import bisect
import datetime as dt
import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

#: inline mark for a value or paragraph the evidence does not support
MARK = "(확인 필요)"

#: value blanks the guards write; fill-in fields (「[담당자]」 「[문의처]」) are not among them
GUARD_BLANK = re.compile(
    r"\[(?:구체적\s?수치|수치|금액|날짜|일자|일시|시기|시각|시간|배포일|버전|측정값|인원|정도)\]")

_WD = "월화수목금토일"
#: 「2026-11-04」, 「2026. 11. 4.」, 「2026년 11월 4일」, 「2026/11/4」, weekday optional
_FULL_DAY = re.compile(
    r"(?<![\d.])(?P<y>(?:19|20)\d{2})\s{0,2}(?:년|[-./])\s{0,2}(?P<m>\d{1,2})\s{0,2}(?:월|[-./])"
    rf"\s{{0,2}}(?P<d>\d{{1,2}})(?!\d)\s{{0,2}}(?:일|\.)?(?:\s{{0,2}}\(\s?[{_WD}](?:요일)?\s?\))?")
#: 「11월 4일」, 「11월 4일(수)」
_MONTH_DAY = re.compile(
    r"(?<![\d.])(?P<m>\d{1,2})\s{0,2}월\s{0,2}(?P<d>\d{1,2})\s{0,2}일"
    rf"(?:\s{{0,2}}\(\s?[{_WD}](?:요일)?\s?\))?")
#: 「11/4」, 「11. 4.」, 「11.4(수)」; not a version (「3.4.1」) nor a fraction run
_SHORT_DAY = re.compile(
    rf"(?<![\d./])(?P<m>\d{{1,2}})\s{{0,1}}(?:/|\.\s{{0,1}})(?P<d>\d{{1,2}})(?![\d/]|\.\d)\.?"
    rf"(?:\s{{0,2}}\(\s?[{_WD}](?:요일)?\s?\))?")
#: 「2026. 11.」, 「2026년 11월」: a month of a year
_YEAR_MONTH = re.compile(
    r"(?<![\d.])(?P<y>(?:19|20)\d{2})\s{0,2}(?:년\s{0,2}|[.-]\s{0,2})(?P<m>\d{1,2})(?:\s{0,2}월|\.)?"
    r"(?![\d-]|\s{0,2}\d|\s{0,2}일)")
#: 「11월」 alone
_MONTH = re.compile(r"(?<![\d.])(?P<m>\d{1,2})\s{0,2}월(?!\s{0,2}\d{1,2}\s{0,2}일)")
#: a duration a date may be moved by: 「3주」, 「14일」, 「2개월」 (not 영업일)
_SPAN = re.compile(
    r"(?<![\d.영업])(?P<n>\d{1,3})\s{0,2}(?:(?P<u>주일|주|개월|달)(?!차)"
    r"|(?P<d>일)(?=\s{0,2}(?:간|동안|전|후|뒤|이내|안에|만에|이후|이전|가량|정도|짜리)))")

#: a number with its scale words: 「5억 7,499만」, 「8억 8천만」, 「1.2조」, 「26」
_TERM = (r"\d(?:,?\d)*(?:\.\d+)?\s?(?:조|억|천만|백만|만|천)"
         r"(?:\s?\d(?:,?\d)*(?:\.\d+)?\s?(?:억|천만|백만|만|천))*"
         r"|\d(?:,?\d)*(?:\.\d+)?")
_UNITS = ("%p", "%", "퍼센트", "원", "달러", "명", "건", "개월", "개", "가지", "곳", "년", "주",
          "일", "시간", "분", "초", "회", "배", "점", "자", "줄", "장", "쪽", "대", "팀")
_UNIT = "|".join(map(re.escape, _UNITS))
_NUMBER = re.compile(rf"(?<![A-Za-z\d.,_/#-])(?P<num>{_TERM})(?![\d])\s?(?P<unit>{_UNIT})?")
_FAMILY = {"퍼센트": "%", "가지": "개"}

#: order and structure after a number: 「1차」, 「2번째」, 「3단계」, 「4절」
_ORDINAL_AFTER = re.compile(
    r"\s?(?:차(?!이)|번째|번(?!역)|단계|순위|위(?![원험치해])|호(?![텔응출])|교시|주차|라운드|레벨"
    r"|등급|안(?=[\s,)·]|$))")
#: an item counted against the answer's own structure: 「항목 7개」, 「3가지」, 「2건」
_COUNT_UNIT = ("개", "가지", "건", "곳", "줄", "장", "팀")
#: the words of a count: 「7개 항목」, 「3가지」, 「2건」
_COUNTED = re.compile(r"항목|개\s?(?:항목|중)|가지|건|개(?:를|가|는|만|씩|의)?(?:\s|$|[,.)])")
#: a character count note: 「(제목 26자)」, 「26자 이내」
_CHARS_AFTER = re.compile(r"\s?(?:자|글자|바이트|byte)(?![가-힣])")
_LIST_MARK = re.compile(r"^\s{0,8}(?:[-*+•]\s{1,4})?(?:#\s?|\(?)\d{1,3}[.)]?\s")
_BEFORE_ORDINAL = re.compile(
    r"(?:제|#|No\.?|STEP|Step|단계|표|그림|붙임|별첨|부록|문항|사례|안건|Q)\s?$")
_QUOTED = re.compile(r"「([^」\n]{1,200})」|“([^”\n]{1,200})”|\"([^\"\n]{1,200})\"")

_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
_PLAIN = frozenset({
    "있습", "있다", "합니", "됩니", "하는", "되는", "이후", "이전", "경우", "모든", "기존",
    "해당", "대한", "위해", "통해", "따라", "니다", "그리", "또는", "이용", "사용", "있는",
    "없는", "있으", "하고", "하며", "에서", "으로", "것으", "같은", "이번", "확인"})
#: a month with its part: 「11월 초」, 「3월 중순」
_MONTH_PART = re.compile(r"(?<![\d.])(?P<m>\d{1,2})\s{0,2}월(?:\s{0,2}(?P<part>초|중순|하순|말))?")


def ko_number(raw: str) -> float:
    """「4억 8,000만」 → 480000000; 「8억 8천만」 → 880000000; 「12.4」 → 12.4."""
    text = raw.replace(" ", "")
    text = re.sub(r"(\d[\d,]{0,20}(?:\.\d{1,15})?)천(?=만|억|$)",
                  lambda m: f"{float(m.group(1).replace(',', '')) * 1000:g}", text)
    text = re.sub(r"(\d[\d,]{0,20}(?:\.\d{1,15})?)백만",
                  lambda m: f"{float(m.group(1).replace(',', '')) * 100:g}만", text)
    text = re.sub(r"[^\d.,조억만]", "", text)
    total, rest = 0.0, text
    for unit, scale in (("조", 1e12), ("억", 1e8), ("만", 1e4)):
        if unit in rest:
            head, rest = rest.split(unit, 1)
            total += float(head.replace(",", "") or 0) * scale
    if rest.replace(",", ""):
        total += float(rest.replace(",", ""))
    return total


def _step(raw: str) -> float:
    """Half the place of the last written digit: 「887만」 → 5000, 「65.3」 → 0.05."""
    text = raw.replace(" ", "").replace(",", "")
    last = re.findall(r"(\d+(?:\.\d+)?)(조|억|천만|백만|만|천)?", text)
    if not last:
        return 0.5
    digits, scale = last[-1]
    places = len(digits.split(".", 1)[1]) if "." in digits else 0
    factor = {"조": 1e12, "억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3}.get(scale, 1.0)
    return 0.5 * 10.0 ** -places * factor


def _family(unit: str | None) -> str:
    return _FAMILY.get(unit or "", unit or "")


def _days_in(text: str) -> list[tuple[int | None, int, int, tuple[int, int]]]:
    """Every day `text` writes, any notation: (year or None, month, day, span)."""
    out: list[tuple[int | None, int, int, tuple[int, int]]] = []
    taken: list[tuple[int, int]] = []
    for pattern in (_FULL_DAY, _MONTH_DAY, _SHORT_DAY):
        for m in pattern.finditer(text or ""):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            y = int(m.group("y")) if "y" in m.groupdict() and m.group("y") else None
            mo, d = int(m.group("m")), int(m.group("d"))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                taken.append(m.span())
                out.append((y, mo, d, m.span()))
    return out


@dataclass(slots=True)
class Evidence:
    """What the answer may say: the person's words and files, answers, tool results."""

    texts: list[str]
    #: the answer as written before the guard: its tables count, its own line never
    answer: str = ""
    today: dt.date | None = None
    days: set[tuple[int | None, int, int]] = field(default_factory=set)
    months: set[int] = field(default_factory=set)
    years: set[int] = field(default_factory=set)
    spans: set[int] = field(default_factory=set)
    values: list[tuple[float, str]] = field(default_factory=list)
    sorted_values: list[float] = field(default_factory=list)
    derived: list[float] = field(default_factory=list)
    derived_rows: list[float] = field(default_factory=list)
    flat: str = ""
    request: str = ""
    #: most items any one evidence text lists (a change list of 7 lines)
    listed: int = 0
    #: the answer's own table rows (row, its values, its days): evidence for its prose
    rows: list[tuple[str, list[tuple[float, str]], set[tuple[int, int]]]] = field(
        default_factory=list)
    #: item counts of the lists the evidence enumerates (「네이버웍스, 카카오워크, … 슬랙」 → 7)
    enumerated: set[int] = field(default_factory=set)
    #: the answer's own figures worked step by step from the evidence (a total, its VAT, …)
    chained: list[float] = field(default_factory=list)
    #: the evidence prices with VAT
    vat: bool = False
    #: periods the evidence gives (「기간 11/1~11/30」): a day in one is the writer's schedule
    periods: list[tuple[tuple[int, int], tuple[int, int]]] = field(default_factory=list)

    #: the whole document the answer is a part of: its worked formulas count
    worked: str = ""

    @classmethod
    def of(cls, texts: Iterable[str], *, answer: str = "", request: str = "",
           today: dt.date | None = None, worked: str = "") -> Evidence:
        own = [t for t in texts if t and t.strip()]
        ev = cls(texts=own, answer=answer or "", today=today, request=request or "",
                 worked=worked or "")
        ev.listed = max([_structure_count(t) for t in [*own, request or ""]] or [0])
        for line in (answer or "").split("\n"):
            if line.strip().startswith("|") and not re.fullmatch(r"[\s|:\-]+", line.strip()):
                ev.rows.append((line.strip(), _values(line),
                                {(m, d) for _, m, d, _ in _days_in(line)}))
        ev._read([*own, request or ""])
        if today is not None:
            # a document dated today (「보고일: 2026. 10. 8.」) states no fact; nor does this month
            ev.days.update({(today.year, today.month, today.day), (None, today.month, today.day)})
            ev.years.add(today.year)
            ev.months.add(today.month)
        ev._chain()
        return ev

    def _read(self, texts: list[str]) -> None:
        joined = "\n".join(texts)
        self.flat = re.sub(r"\s+", "", joined).lower()
        for y, m, d in _url_days(joined):
            self.days.update({(y, m, d), (None, m, d)})
            self.years.add(y)
            self.months.add(m)
        self.enumerated = _enumerations(joined)
        self.periods = _periods(joined)
        for y, m, d, _ in _days_in(joined):
            self.days.update({(y, m, d), (None, m, d)})
            self.months.add(m)
            if y:
                self.years.add(y)
        for m in _MONTH.finditer(joined):
            if 1 <= int(m.group("m")) <= 12:
                self.months.add(int(m.group("m")))
        for m in _YEAR_MONTH.finditer(joined):
            if 1 <= int(m.group("m")) <= 12:
                self.months.add(int(m.group("m")))
        self.years |= {int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", joined)}
        for m in _SPAN.finditer(joined):
            n = int(m.group("n"))
            unit = m.group("u") or "일"
            if 0 < n <= 400:
                weeks = 7 if unit.startswith("주") else 1
                self.spans.add(-n if unit in ("개월", "달") else n * weeks)
        self.values = _values(joined)
        self.sorted_values = sorted({abs(v) for v, _ in self.values if v})
        own_rows = [x for r in self.rows for x in r[1]]
        tables = [*_table_sums(joined), *(_table_sums(self.answer) if own_rows else [])]
        self.vat = bool(_VAT.search(joined))
        self.derived = sorted({*_derived(self.sorted_values, vat=self.vat), *_table_sums(joined)})
        self.derived_rows = sorted({
            *_derived(sorted({abs(v) for v, _ in [*self.values, *own_rows] if v}), vat=self.vat),
            *tables, *_row_steps(self.rows, {abs(v) for v, _ in _values(self.answer)}),
        }) if own_rows else self.derived

    def _chain(self, rounds: int = 6) -> None:
        """The answer's figures each one simple step from the evidence or an earlier such
        figure: 「1,480,000원×30대=44,400,000원」, its 5% 할인, the VAT on that, the total."""
        pool = set(self.sorted_values)
        todo = {abs(v) for v, _ in _values(self.answer) if abs(v) >= 10} - pool
        if not todo or len(todo) > 400:
            return
        known: set[float] = set()
        formulas = _formulas(f"{self.answer}\n{self.worked}")
        for _ in range(rounds):
            near = sorted({*_derived(sorted(pool | known), vat=self.vat), *self.derived})
            added = {v for v in _worked(formulas, pool | known, self.vat) if v in todo}
            for v in todo:
                tol = 0.5 + 1e-9 * v
                k = bisect.bisect_left(near, v - tol)
                if k < len(near) and near[k] <= v + tol:
                    added.add(v)
            if not added:
                break
            known |= added
            todo -= added
        self.chained = sorted(known)

    def supports_number(self, value: float, unit: str, raw: str, line: str = "") -> bool:
        tol = _step(raw) + 1e-9 * max(1.0, abs(value))
        family = _family(unit)
        v = abs(value)
        if family in _COUNT_UNIT and v == int(v) and int(v) in self.enumerated:
            # 「경쟁사 7곳」 for the seven names the person listed
            return True
        if v >= 10 and self.chained:
            k = bisect.bisect_left(self.chained, v - tol)
            if k < len(self.chained) and self.chained[k] <= v + tol:
                return True
        strict = family in ("%", "%p", "원", "달러")
        lo = bisect.bisect_left(self.sorted_values, v - tol)
        hi = bisect.bisect_right(self.sorted_values, v + tol)
        if lo < hi:
            if not strict:
                return True
            if any(abs(abs(k) - v) <= tol and (not f or _family(f) == family)
                   for k, f in self.values):
                return True
        own = line.strip()
        # the answer's tables back its prose, not one another
        for _, values, _ in [] if own.startswith("|") else self.rows:
            if any(abs(abs(k) - v) <= tol and (
                    not strict or not f or _family(f) == family) for k, f in values):
                return True
        if v >= 10 or "." in raw or family in ("%", "%p"):
            # a table cell from the given figures only; prose from those and the tables too
            derived = self.derived if own.startswith("|") else self.derived_rows
            lo = bisect.bisect_left(derived, v - tol)
            hi = bisect.bisect_right(derived, v + tol)
            if lo < hi:
                return True
        return False

    def supports_day(self, y: int | None, m: int, d: int, line: str = "") -> bool:
        if (y, m, d) in self.days or (None, m, d) in self.days:
            return True
        if any(a <= (m, d) <= b for a, b in self.periods):
            return True
        if not line.strip().startswith("|") and any((m, d) in days for _, _, days in self.rows):
            return True
        return self._moved(y, m, d)

    def _moved(self, y: int | None, m: int, d: int) -> bool:
        """A given day moved by a given span: 「10월 12일 계약, 납기 3주」 → 11월 2일."""
        if not self.spans:
            return False
        years = {y} if y else (self.years or ({self.today.year} if self.today else set()))
        if not years:
            years = {dt.date.today().year}
        targets = []
        for year in years:
            try:
                targets.append(dt.date(year, m, d))
            except ValueError:
                continue
        bases = []
        for by, bm, bd in self.days:
            for year in ({by} if by else years):
                try:
                    bases.append(dt.date(year, bm, bd))
                except ValueError:
                    continue
        for target in targets:
            for base in bases:
                gap = (target - base).days
                if abs(gap) in self.spans:
                    return True
                months = (target.year - base.year) * 12 + target.month - base.month
                if target.day == base.day and -abs(months) in self.spans:
                    return True
        return False

    def covers(self, text: str) -> bool:
        """Whether the evidence holds most of the words of `text` (a paragraph or sentence)."""
        words = {w[:2] if w[0] >= "가" else w.lower() for w in _WORD.findall(text)} - _PLAIN
        if not words:
            return True
        hit = sum(1 for w in words if w in self.flat)
        return hit >= 0.7 * len(words)


#: a worked formula on one line: 「(1,480,000 × 30 × 0.95 + 5,000 × 30) × 1.1 = 46,563,000원」
_FORMULA = re.compile(
    r"(?P<lhs>[(\d][\d,.\s()×xX*÷/+\-−원대명개건%]*[\d)%원대명개건])\s{0,3}=\s{0,3}"
    r"(?P<rhs>\d[\d,]*(?:\.\d+)?)")
_OPERAND = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def _formulas(answer: str) -> list[tuple[str, list[str], float]]:
    """`(python expression, operands, stated result)` of the answer's written formulas."""
    out = []
    lines = [line for line in (answer or "").split("\n") if "=" in line]
    # label words between terms go: 「확정 42,000,000원(부가세 포함) + 보증 연장 45,000원 × 30대」
    cleaned = [re.sub(r"[가-힣A-Za-z]{1,12}\s{0,2}[:：]", " ", re.sub(
        r"\([^()\n]*[가-힣][^()\n]*\)|(?<![\d])[가-힣]{1,12}(?![\d가-힣])", " ", line))
        for line in lines]
    for m in _FORMULA.finditer("\n".join(cleaned)):
        lhs = m.group("lhs")
        if not re.search(r"[×xX*÷/+\-−]", lhs):
            continue
        expr = re.sub(r"[원대명개건]", "", lhs)
        expr = expr.replace("×", "*").replace("x", "*").replace("X", "*").replace("÷", "/")
        expr = expr.replace("−", "-")
        operands = _OPERAND.findall(expr)
        expr = re.sub(r"(\d[\d,]*(?:\.\d+)?)%", r"(\1/100)", expr).replace(",", "")
        if not re.fullmatch(r"[\d.\s()*/+\-]+", expr) or "**" in expr.replace(" ", ""):
            continue
        try:
            out.append((expr, operands, float(m.group("rhs").replace(",", ""))))
        except ValueError:
            continue
    return out


def _worked(formulas: list[tuple[str, list[str], float]], known: set[float],
            vat: bool) -> set[float]:
    """Stated results of formulas whose operands are all known (or a rate) and whose
    arithmetic holds."""
    out: set[float] = set()
    shares = {v for v in known if 0 < v < 100}
    rates = {1.0, 100.0, *({1.1, 0.1} if vat else ()),
             *(round(1 - v / 100, 6) for v in shares), *(round(v / 100, 6) for v in shares)}
    for expr, operands, stated in formulas:
        ok = True
        for raw in operands:
            v = float(raw.rstrip("%").replace(",", ""))
            if raw.endswith("%"):
                v_ok = v in shares
            else:
                v_ok = v in rates or any(abs(v - k) <= 0.5 + 1e-9 * v for k in known)
            if not v_ok:
                ok = False
                break
        if not ok:
            continue
        try:
            value = eval(compile(expr, "<formula>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
        except Exception:  # noqa: BLE001 — not arithmetic after all
            continue
        if isinstance(value, (int, float)) and abs(value - stated) <= max(0.5, 1e-4 * stated):
            out.add(stated)
    return out


#: a material that prices with VAT: 「부가세 별도」, 「VAT 포함」
_VAT = re.compile(r"부가세|부가가치세|VAT", re.I)
#: a date in a link: 「/2026/03/15/」 is a day too; 「/202610021506176780」 is 2026-10-02
_URL = re.compile(r"https?://\S+")
_URL_DAY = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?=\d{0,12}(?!\d))")


def _url_days(text: str) -> set[tuple[int, int, int]]:
    """Days a search result's links carry in compact form (a news id starting 「20261002」)."""
    out = set()
    for url in _URL.findall(text or ""):
        for m in _URL_DAY.finditer(url):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.add((y, mo, d))
    return out


#: a period: 「11/1~11/30」, 「11월 1일 ~ 30일」, 「2026-11-01 ~ 2026-11-30」
_PERIOD_SEP = re.compile(r"\s{0,2}[~∼〜–—-]\s{0,2}|\s{0,2}부터\s{0,2}")


def _periods(text: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """(first, last) month-day pairs of the periods `text` gives, a year at most."""
    days = sorted(_days_in(text or ""), key=lambda t: t[3][0])
    out = []
    for (_, m1, d1, (_, e1)), (_, m2, d2, (s2, _)) in zip(days, days[1:], strict=False):
        if _PERIOD_SEP.fullmatch(text[e1:s2]) and (m1, d1) < (m2, d2):
            out.append(((m1, d1), (m2, d2)))
    # 「11월 1일~30일」: the end day without its month
    for m in re.finditer(r"(?<![\d.])(\d{1,2})\s{0,2}월\s{0,2}(\d{1,2})\s{0,2}일"
                         r"\s{0,2}[~∼〜–-]\s{0,2}(\d{1,2})\s{0,2}일", text or ""):
        mo, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and a < b <= 31:
            out.append(((mo, a), (mo, b)))
    return out


#: one listed item: a short name between commas (「잔디」, 「마이크로소프트 팀즈」)
_ITEM = re.compile(r"^[가-힣A-Za-z0-9][가-힣A-Za-z0-9 .&+-]{0,23}$")


def _enumerations(text: str) -> set[int]:
    """Item counts of comma lists of three or more short names, in brackets or on a line."""
    out: set[int] = set()
    pieces = [m.group(1) for m in re.finditer(r"[(（]([^()（）\n]{4,400})[)）]", text or "")]
    pieces += [line for line in (text or "").split("\n")]
    for piece in pieces:
        for run in re.split(r"[(（)）:：;]", piece):
            parts = [re.sub(r"\s{0,2}등$", "", p.strip()) for p in re.split(r"[,，、]", run)]
            if len(parts) >= 3 and all(_ITEM.match(p) for p in parts):
                out.add(len(parts))
    return out


def _derived(pool: list[float], *, vat: bool = False) -> list[float]:
    """Simple arithmetic of two figures: sums, gaps, shares, changes, price × count; with a
    VAT in the material, a figure with or without its 10% and that 10% alone."""
    big = [v for v in pool if v >= 10][-80:]
    counts = [v for v in pool if 1 < v < 100 and v == int(v)][:40]
    out: set[float] = set()
    shares = [v for v in pool if 0 < v < 100]
    if vat:
        for a in big:
            out.update((a * 1.1, a * 0.1, a / 1.1, a / 11))
            for c in counts:
                out.update((a * c * 1.1, a * c * 0.1))
    # a discount taken off a figure: 「총액 5% 할인」 → 95%
    out.update((100 - v) / 100 * w for v in shares for w in big[-40:])
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            out.update((a + b, b - a, b / a * 100, a / b * 100, (b - a) / a * 100,
                        (b - a) / b * 100, b / a))
    for a in big:
        out.update(a * c for c in counts)
        out.update(a / c for c in counts)
    out.update(v / 100 * w for v in pool if 0 < v <= 100 for w in big[-40:])
    # the rest of a share: 「70% 작성」 → 「잔여 30%」
    out.update(100 - v for v in pool if 0 < v < 100)
    if len(big) >= 2:
        out.add(sum(big))
    if len(big) <= 25:
        # a short material: a price times a count, plus or less another figure; two or three
        # unit prices for the same count (단가 + 연장 보증 + 수거비, × 30대)
        for a in big:
            for c in counts[:12]:
                out.update(x for b in big if b != a for x in (a * c + b, a * c - b))
        units_ = big[:25]
        for i, a in enumerate(units_):
            for j, b in enumerate(units_[i + 1:], i + 1):
                pair = a + b
                out.update(pair * c for c in counts[:12])
                out.update((pair + d) * c for d in units_[j + 1:] for c in counts[:12])
    return sorted(out)


def _table_sums(text: str) -> list[float]:
    """Each column total of the markdown tables in `text`: 「집행액 합」 summed."""
    out: list[float] = []
    block: list[list[str]] = []
    for line in [*(text or "").split("\n"), ""]:
        if line.strip().startswith("|"):
            if not re.fullmatch(r"[\s|:\-]+", line.strip()):
                block.append([c.strip(" *") for c in line.strip().strip("|").split("|")])
            continue
        for k in range(max((len(r) for r in block), default=0)):
            cells = [r[k] for r in block[1:] if k < len(r)]
            values = [ko_number(c) for c in cells
                      if re.fullmatch(rf"(?:{_TERM})\s?(?:원|%|명|건)?", c)]
            if len(values) >= 2:
                out.append(sum(abs(v) for v in values))
        block = []
    return out


def _row_steps(rows: list[tuple[str, list[tuple[float, str]], set]],
               counts: set[float]) -> list[float]:
    """Two steps within one table row: a monthly rate times the months said, less what is left."""
    out: set[float] = set()
    steps = sorted(c for c in counts if 2 <= c <= 12 and c == int(c))
    for _, values, _ in rows:
        cells = sorted({abs(v) for v, _ in values if abs(v) >= 10})[:12]
        for a in cells:
            for c in steps:
                for b in cells:
                    if b != a:
                        out.update((a * c - b, a * c + b))
    return [v for v in out if v > 0]


def _values(text: str) -> list[tuple[float, str]]:
    """Numbers in `text` with their unit family, dates set aside."""
    masked = text
    for _, _, _, (s, e) in sorted(_days_in(text), key=lambda t: -t[3][0]):
        masked = masked[:s] + " " * (e - s) + masked[e:]
    out: list[tuple[float, str]] = []
    for m in _NUMBER.finditer(masked):
        try:
            out.append((ko_number(m.group("num")), _family(m.group("unit"))))
        except ValueError:
            continue
    # a count said in words: 「두 명」, 「세 곳」
    for word, n in (("한", 1), ("두", 2), ("세", 3), ("네", 4), ("다섯", 5), ("여섯", 6),
                    ("일곱", 7), ("여덟", 8), ("아홉", 9), ("열", 10)):
        if re.search(rf"(?<![가-힣]){word}\s?(?:명|개|곳|건|가지|팀)", text):
            out.append((float(n), ""))
    return out


def _structure_count(text: str) -> int:
    """Items the answer itself lists: list items and table data rows."""
    items = sum(1 for line in text.split("\n")
                if re.match(r"^\s{0,8}(?:[-*+•]|\d{1,3}[.)])\s", line))
    rows = sum(1 for line in text.split("\n") if line.strip().startswith("|")
               and not re.fullmatch(r"[\s|:\-]+", line.strip()))
    return items + rows


#: a table column of order numbers or of character counts: 「#」, 「번호」, 「글자 수」
_ORDER_COLUMN = re.compile(r"^(?:#|No\.?|번호|순번|순위|순|행|안)$", re.I)
#: a column of character counts: its value is measured against the row's own text
_CHARS_COLUMN = re.compile(r"글자|자\s?수|길이|chars?|bytes?", re.I)


def _measured(line: str, col: int, value: float) -> bool:
    """Whether `value` is the length of another cell of the row (with or without spaces)."""
    cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
    for k, cell in enumerate(cells):
        if k == col or not cell:
            continue
        bare = re.sub(r"^[「“\"']|[」”\"']$", "", cell)
        if value in (len(bare), len(bare.replace(" ", "")), len(cell)):
            return True
    return False


def _table_header(text: str, pos: int) -> list[str]:
    """Header cells of the table holding `pos`, or []."""
    lines = text[:pos].split("\n")
    if not lines[-1].lstrip().startswith("|") and not text[pos - len(lines[-1]):].lstrip(
            ).startswith("|"):
        return []
    k = len(lines) - 1
    while k > 0 and lines[k - 1].strip().startswith("|"):
        k -= 1
    head = lines[k] if k < len(lines) - 1 else text[pos - len(lines[-1]):].split("\n", 1)[0]
    return [c.strip(" *") for c in head.strip().strip("|").split("|")]


def _exempt(line: str, start: int, end: int, raw: str, unit: str, ev: Evidence,
            structure: int, header: list[str] | None = None) -> bool:
    """Ordinals, step numbers, own-item counts, character counts, request years, quotes."""
    after = line[end:end + 6]
    before = line[max(0, start - 8):start]
    value = ko_number(raw)
    if header and line.lstrip().startswith("|"):
        col = line[:start].count("|") - 1
        if 0 <= col < len(header) and _ORDER_COLUMN.search(header[col].strip()):
            return True
        if 0 <= col < len(header) and _CHARS_COLUMN.search(header[col]):
            # a count the row's own text bears out; a miscount is the writer's to fix
            return _measured(line, col, value)
    if not unit and _ORDINAL_AFTER.match(after):
        return True
    if unit in ("주", "회") and line.lstrip().startswith("|") and line[:start].count("|") == 1 \
            and re.fullmatch(r"\|\s*\**", line[:start].strip()) and value <= max(structure, 12):
        # a row label counting the rows: 「| 4주 | 11/23(월) | …」
        return True
    if unit in ("자",) or (not unit and _CHARS_AFTER.match(after)):
        return True
    if _BEFORE_ORDINAL.search(before) or before.endswith("#"):
        return True
    if _LIST_MARK.match(line) and start <= len(line) - len(line.lstrip()) + 4:
        return True
    if (unit in _COUNT_UNIT or unit == "") and value == int(value) \
            and 0 < value <= max(structure, 3) and _COUNTED.search(line[start:end + 8]):
        return True
    if unit == "년" or (not unit and re.fullmatch(r"(?:19|20)\d{2}", raw.strip())):
        if int(value) in ev.years or str(int(value)) in ev.request:
            return True
    for q in _QUOTED.finditer(line):
        if q.start() <= start and end <= q.end():
            quoted = next(g for g in q.groups() if g)
            if re.sub(r"\s+", "", quoted).lower() in ev.flat:
                return True
    return False


#: a step the writer proposes, not a fact it reports: 「11월, 인력 계획을 확정합니다」
_PLAN = re.compile(
    r"(?:확정|진행|준비|착수|추진|시행|검토|확인|등록|결정|정리|수립|마련|점검)(?:합니다|하겠습니다|한다|할\s?예정|"
    r"하도록|해야|을\s?마칩니다|를\s?마칩니다)|(?:정합니다|마칩니다|엽니다|둡니다)")
_PLAN_NOT = re.compile(r"발표|공개|개정|시행령|법률|정부|부처")


def _planned(y: int, mo: int, line: str, ev: Evidence) -> bool:
    """A month up to two years ahead in a line that proposes a step of the reader's own."""
    if ev.today is None or not y or not 1 <= mo <= 12:
        return False
    ahead = (y - ev.today.year) * 12 + mo - ev.today.month
    return 0 <= ahead <= 24 and bool(_PLAN.search(line)) and not _PLAN_NOT.search(line)


def supported(segment: str, line: str, offset: int, ev: Evidence, structure: int,
              header: list[str] | None = None) -> bool:
    """Whether every value in `segment` (at `offset` in `line`) is supported or exempt."""
    days = _days_in(segment)
    for y, m, d, _ in days:
        if not ev.supports_day(y, m, d, line):
            return False
    masked = segment
    for _, _, _, (s, e) in sorted(days, key=lambda t: -t[3][0]):
        masked = masked[:s] + " " * (e - s) + masked[e:]
    for m in _YEAR_MONTH.finditer(masked):
        y, mo = int(m.group("y")), int(m.group("m"))
        if 1 <= mo <= 12 and not (y in ev.years and mo in ev.months) and not _planned(
                y, mo, line, ev):
            return False
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    for m in _MONTH_PART.finditer(masked):
        part = m.group("part")
        if not part:
            continue
        mo = int(m.group("m"))
        days = {"초": range(1, 11), "중순": range(11, 21)}.get(part, range(21, 32))
        if re.sub(r"\s+", "", m.group(0)) not in ev.flat and not any(
                (None, mo, d) in ev.days for d in days):
            return False
    for m in _MONTH.finditer(masked):
        if int(m.group("m")) not in ev.months and not _planned(
                ev.today.year if ev.today else 0, int(m.group("m")), line, ev) and not _exempt(
                line, offset + m.start(), offset + m.end("m"), m.group("m"), "", ev, structure):
            return False
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    for m in _NUMBER.finditer(masked):
        raw, unit = m.group("num"), m.group("unit") or ""
        try:
            value = ko_number(raw)
        except ValueError:
            continue
        if value in (0, 1) and not unit:
            continue
        start, end = offset + m.start("num"), offset + m.end("num")
        if _exempt(line, start, end, raw, unit, ev, structure, header):
            continue
        if not ev.supports_number(value, unit, raw, line):
            return False
    return True


#: a date part or a blank standing for one
_PART = r"(?:\d{1,4}|\[[^\[\]\n]{1,12}\])"
#: a whole date, any notation, blanks inside it too (「2026년 [수치]월 [수치]일」): one token, so
#: a value given back or marked is the whole date, never a piece of it
_DATE_TOKEN = (
    rf"{_PART}\s?(?:년|[-./])\s?{_PART}\s?(?:월|[-./])\s?{_PART}\s?(?:일)?"
    rf"(?:\s?\(\s?[{_WD}](?:요일)?\s?\))?"
    rf"|{_PART}\s?월\s?{_PART}\s?일(?:\s?\(\s?[{_WD}](?:요일)?\s?\))?")
_TOKEN = re.compile(rf"{_DATE_TOKEN}|\[[^\[\]\n]{{1,30}}\]|\d[\d,.]*|[A-Za-z]+|\s+|.", re.S)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text or "")


@dataclass(slots=True)
class Settled:
    text: str
    #: blanked values put back as written (supported or exempt)
    restored: list[str] = field(default_factory=list)
    #: blanked values put back with 「(확인 필요)」
    marked: list[str] = field(default_factory=list)
    #: paragraphs or tables the guard emptied, kept
    kept: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.restored or self.marked or self.kept)

    def note(self) -> str:
        """One line for the step detail, never the body."""
        parts = []
        if self.restored:
            parts.append(f"근거 있는 값 되살림 {len(self.restored)}개")
        if self.marked:
            parts.append(f"확인 필요 표시 {len(self.marked)}개: "
                         + ", ".join(f"「{v[:20]}」" for v in self.marked[:6]))
        if self.kept:
            parts.append(f"비워질 문단·표 유지 {len(self.kept)}개")
        return " · ".join(parts)


def _line_at(text: str, pos: int) -> tuple[str, int]:
    """The line holding `pos` and its start offset."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end >= 0 else len(text)], start


def _widen(text: str, s: int, e: int) -> tuple[int, int]:
    """Span grown to the whole value: digits, scale words, unit, a date's parts and weekday."""
    while s > 0 and (text[s - 1].isdigit()
                     or (text[s - 1] in ",." and s > 1 and text[s - 2].isdigit())):
        s -= 1
    while e < len(text) and (text[e].isdigit()
                             or (text[e] in ",." and text[e + 1 : e + 2].isdigit())):
        e += 1
    line, base = _line_at(text, s)
    for *_, (ds, de) in _days_in(line):
        if base + ds <= s < base + de or base + ds < e <= base + de:
            s, e = min(s, base + ds), max(e, base + de)
    tail = re.match(rf"\s?(?:조|억|천만|백만|만|천)?\s?(?:{_UNIT}|월(?:\s?(?:초|중순|하순|말))?)?",
                    text[e:])
    if tail and tail.group(0).strip():
        e += len(tail.group(0).rstrip())
    return s, e


def _header_row(text: str, base: int, line: str) -> bool:
    """Whether `line` (starting at `base`) is a table's header row: a row with the rule under it."""
    if not line.lstrip().startswith("|"):
        return False
    after = text.find("\n", base)
    if after < 0:
        return False
    rule, _ = _line_at(text, after + 1)
    return bool(re.match(r"^\s*\|?\s*:?-{2,}", rule))


def _value_end(text: str, s: int, e: int, widened: int) -> int:
    """End of the last value touching [s, e): its digits, scale word, unit or whole date."""
    line, base = _line_at(text, s)
    end = None
    for _, _, _, (ds, de) in _days_in(line):
        if base + ds < e and s < base + de:
            end = max(end or 0, base + de)
    for m in _NUMBER.finditer(line):
        if base + m.start() < e and s < base + m.end():
            end = max(end or 0, base + m.end())
    for pattern in (_MONTH_PART, _YEAR_MONTH):
        for m in pattern.finditer(line):
            if base + m.start() < e and s < base + m.end():
                end = max(end or 0, base + m.end())
    end = end if end is not None else widened
    while end > s and text[end - 1].isspace():
        end -= 1
    return end


def _settle_values(before: str, after: str, ev: Evidence, structure: int,
                   out: Settled) -> str:
    """`after` with each guard blank replaced by what `before` had there."""
    if not GUARD_BLANK.search(after) and after.count(MARK) <= before.count(MARK):
        return after
    a, b = _tokens(before), _tokens(after)
    offsets = [0]
    for tok in a:
        offsets.append(offsets[-1] + len(tok))
    pieces: list[str] = []
    #: offset in `before` after which an unsupported value's mark goes
    pending: int | None = None
    marked_at: set[int] = set()

    def flush(upto: int, text: str, at: int) -> str:
        nonlocal pending
        if pending is None or pending > upto:
            return text
        cut = max(0, pending - at)
        head, tail = text[:cut], text[cut:]
        pending = None
        if tail.lstrip().startswith(MARK):
            return text
        return head + MARK + tail

    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        new = "".join(b[j1:j2])
        if op == "equal":
            pieces.append(flush(offsets[i2], new, offsets[i1]))
            continue
        if pending is not None and pending <= offsets[i1]:
            pieces.append(flush(offsets[i1], "", offsets[i1]))
        was = "".join(a[i1:i2])
        # a value the guard swapped for 「(확인 필요)」 is a blank too (「스크립트 지적 9개」)
        swapped = MARK in new and MARK not in was and re.search(r"\d", was)
        if not GUARD_BLANK.search(new) and not swapped:
            pieces.append(new)
            continue
        if i1 == i2:
            # a blank with no original under it: nothing to give back
            pieces.append(GUARD_BLANK.sub("", new))
            continue
        old = before[offsets[i1]:offsets[i2]]
        s, e = _widen(before, offsets[i1], offsets[i2])
        line, base = _line_at(before, s)
        whole = before[s:e]
        if _header_row(before, base, line):
            # a column name (「12월말 예상 집행액」) labels values, it claims none
            out.restored.append(whole.strip())
            pieces.append(old)
            continue
        if supported(whole, line, s - base, ev, structure, _table_header(before, s)):
            out.restored.append(whole.strip())
            pieces.append(old)
            continue
        end = _value_end(before, offsets[i1], offsets[i2], e)
        if end in marked_at or before[end:end + len(MARK) + 1].lstrip().startswith(MARK):
            pieces.append(old)
            continue
        out.marked.append(whole.strip())
        marked_at.add(end)
        if end <= offsets[i2]:
            cut = max(0, end - offsets[i1])
            pieces.append(old[:cut] + MARK + old[cut:])
        else:
            pieces.append(old)
            pending = end
    text = "".join(pieces)
    return text + MARK if pending is not None else text


_TABLE_RULE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _blocks(text: str) -> list[str]:
    return re.split(r"\n[ \t]*\n", text or "")


#: a line that only names what follows: a heading or a bold label (「**고객 사례**」)
_LABEL_LINE = re.compile(r"^\s{0,3}(?:#{1,6}\s.*|\*\*[^*\n]{1,40}\*\*\s{0,2}[:：]?\s*)$", re.M)


def _has_words(block: str) -> bool:
    body = _LABEL_LINE.sub("", block)
    body = re.sub(r"[|:\-*_>#•·\s\d.)(]", "", body)
    return bool(re.search(r"[가-힣A-Za-z]", body))


def _table_rows(block: str) -> int:
    return sum(1 for line in block.split("\n")
               if line.strip().startswith("|") and not _TABLE_RULE.match(line))


def _emptied(old: str, new: str) -> bool:
    """Whether a guard left nothing of a paragraph, or a table with only its header."""
    if not _has_words(old):
        return False
    if old.lstrip().startswith("```"):
        return False
    if not new.strip() or not _has_words(new):
        return True
    rows_old, rows_new = _table_rows(old), _table_rows(new)
    return rows_old >= 2 and rows_new <= 1


def _mark_block(block: str, ev: Evidence) -> str:
    """A kept paragraph, with the mark at its end unless the evidence covers it."""
    if ev.covers(block) or block.lstrip().startswith("|") or MARK in block:
        return block
    lines = block.rstrip().split("\n")
    lines[-1] = lines[-1].rstrip() + " " + MARK
    return "\n".join(lines)


def _row_cells(line: str) -> list[str]:
    return line.strip().strip("|").split("|")


def _refill_cells(old: str, new: str, out: Settled) -> str:
    """A table cell a guard emptied, given back as written (r5 A12-T2: the 「추천 채널·활용
    이유」 cells of 25 rows came back blank): rows matched in order, same width only."""
    olds = [line for line in old.split("\n") if line.strip().startswith("|")]
    lines = new.split("\n")
    rows = [i for i, line in enumerate(lines) if line.strip().startswith("|")]
    if not olds or len(olds) != len(rows):
        return new
    for was, i in zip(olds, rows, strict=True):
        before, after = _row_cells(was), _row_cells(lines[i])
        if len(before) != len(after) or _TABLE_RULE.match(was):
            continue
        filled = False
        for c, cell in enumerate(after):
            if before[c].strip() and not re.sub(r"\(?확인\s?필요\)?", "", cell).strip():
                # a cell judged unsupported keeps its words, marked
                verdict = "확인" in cell and MARK not in before[c]
                after[c] = f"{before[c].rstrip()} {MARK} " if verdict else before[c]
                filled = True
        if filled:
            out.kept.append(was.strip()[:60])
            lead = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = f"{lead}|{'|'.join(after)}|"
    return "\n".join(lines)


def settle(before: str, after: str, ev: Evidence) -> Settled:
    """`after` (a guard's output for `before`) with blanks and emptied blocks undone."""
    out = Settled(after)
    if before == after or not before.strip():
        return out
    structure = max(_structure_count(before), ev.listed)
    old_blocks, new_blocks = _blocks(before), _blocks(after)
    matcher = difflib.SequenceMatcher(None, old_blocks, new_blocks, autojunk=False)
    result: list[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            result += new_blocks[j1:j2]
            continue
        if op == "insert":
            result += new_blocks[j1:j2]
            continue
        if op == "delete":
            for block in old_blocks[i1:i2]:
                if _emptied(block, ""):
                    out.kept.append(block.strip()[:60])
                    result.append(_mark_block(block, ev))
            continue
        olds, news = old_blocks[i1:i2], new_blocks[j1:j2]
        if len(olds) == len(news):
            pairs = list(zip(olds, news, strict=True))
        else:
            pairs = [("\n\n".join(olds), "\n\n".join(news))]
        for old, new in pairs:
            if dropped := _dropped_given(old, new, ev):
                # a rewrite took out a value the person gave (「무료 체험 45일」): kept as written
                out.kept.append(f"{dropped}: {old.strip()[:50]}")
                result.append(old)
                continue
            fixed = _unmark(old, _settle_values(old, new, ev, structure, out), ev, structure,
                            out)
            fixed = _refill_cells(old, fixed, out)
            if _emptied(old, fixed):
                out.kept.append(old.strip()[:60])
                fixed = _mark_block(old, ev)
            result.append(fixed)
    out.text = "\n\n".join(result)
    return out


#: a value written just before a mark: a day (weekday too) or a number with its unit
_BEFORE_MARK = re.compile("(?:" + "|".join(
    re.sub(r"\(\?P<\w+>", "(?:", p) for p in (
        _FULL_DAY.pattern, _MONTH_DAY.pattern, _SHORT_DAY.pattern,
        rf"(?<![A-Za-z\d.,_/#-])(?:{_TERM})\s?(?:{_UNIT}|월)?")) + r")\s?$")


def _unmark(before: str, after: str, ev: Evidence, structure: int, out: Settled) -> str:
    """A 「(확인 필요)」 the guard put after a value the evidence supports, taken off; the
    writer's own marks stay."""
    if MARK not in after or after.count(MARK) <= before.count(MARK):
        return after
    pieces, at = [], 0
    for m in re.finditer(re.escape(MARK), after):
        head = after[max(0, m.start() - 40):m.start()]
        value = _BEFORE_MARK.search(head)
        if value is None:
            continue
        written = value.group(0).strip()
        if f"{written}{MARK}" in before.replace(" " + MARK, MARK):
            continue  # the writer marked it
        line, base = _line_at(after, m.start())
        start = m.start() - len(head) + value.start() - base
        if supported(written, line, start, ev, structure, _table_header(after, m.start())):
            out.restored.append(written)
            cut = m.start() - (1 if after[m.start() - 1:m.start()] == " " else 0)
            pieces.append(after[at:cut])
            at = m.end()
    pieces.append(after[at:])
    return "".join(pieces)


def _dropped_given(old: str, new: str, ev: Evidence) -> str:
    """A value `old` writes that the evidence states as written, gone from a non-empty `new`
    the guard rewrote (blanks and marks aside); "" when none."""
    if not new.strip() or GUARD_BLANK.search(new) or old.lstrip().startswith(("|", "```")):
        return ""
    for m in _NUMBER.finditer(old):
        unit = m.group("unit") or ""
        if not unit or unit in ("자", "줄"):
            continue
        written = re.sub(r"\s+", "", m.group(0))
        if written in ev.flat and written not in re.sub(r"\s+", "", new) \
                and old.count(m.group(0)) > new.count(m.group(0)):
            return m.group(0).strip()
    return ""


__all__ = ["GUARD_BLANK", "MARK", "Evidence", "Settled", "ko_number", "settle", "supported"]
