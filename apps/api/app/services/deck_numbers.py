"""Numbers on a written deck held to the material it was made from: a figure with a unit
(a percent, a multiplier, GB, ms, tokens) that no source text has, and a range the source
gives whole (20.4–38.2%) that the slide narrows to one end (「최대 20.4%」). Regex only: no
model, no network; the sources are the texts the person's files hold and what the turn's
tools returned."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUM = r"\d[\d,]*(?:\.\d+)?"
#: the units that make a number a claim: percent, multiplier, sizes, times, tokens
_UNIT = (r"(?:%|퍼센트|×|x(?![A-Za-z])|X(?![A-Za-z])|배(?!치|포|열|경|터)|"
         r"[GMKT]i?B(?![A-Za-z])|ms(?![A-Za-z])|초|tokens?(?![A-Za-z])|토큰)")
_CLAIM = re.compile(rf"(?<![\w.])({_NUM})\s{{0,1}}({_UNIT})")
_ANY = re.compile(rf"(?<![\w.])({_NUM})")
#: a range in a source: 「20.4%–38.2%」, 「2.2–4.3×」, 「2 to 4 times」; the unit on either end
_RANGE = re.compile(
    rf"(?<![\w.])({_NUM})\s{{0,1}}({_UNIT})?\s{{0,2}}(?:–|—|−|-|~|to|에서)\s{{0,2}}"
    rf"({_NUM})\s{{0,1}}({_UNIT})")
#: wording that gives one end of a range as if it were the whole claim
_UPPER = re.compile(r"(?:최대|많아야|이하|불과|up to|at most|no more than|only|just|까지)", re.I)
_LOWER = re.compile(r"(?:최소|이상|at least|no less than|from)", re.I)
_ABOUT = re.compile(r"(?:약|대략|about|around|approximately|~)", re.I)
#: the units that mean the same thing
_SAME = {"×": "x", "X": "x", "x": "x", "배": "x", "퍼센트": "%", "%": "%", "초": "s",
         "토큰": "tok", "token": "tok", "tokens": "tok"}

MAX_SOURCE_CHARS = 1_500_000


def _canon(raw: str) -> str:
    """`1,820.50` → `1820.5`: numbers compared by value, not by how they were typed."""
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return raw
    text = format(value.normalize(), "f")
    return text


def _unit(raw: str) -> str:
    return _SAME.get(raw, raw.lower())


@dataclass(frozen=True)
class Claim:
    slide: int
    text: str
    number: str
    unit: str
    line: str


def claims(slides: list[dict]) -> list[Claim]:
    """Every number-with-unit in each slide's visible text, notes and caption."""
    out: list[Claim] = []
    for index, slide in enumerate(slides, 1):
        lines = [str(slide.get("title") or "")]
        lines += [str(b) for b in slide.get("bullets") or []]
        for row in slide.get("rows") or []:
            lines += [str(cell) for cell in row] if isinstance(row, list) else []
        image = slide.get("image")
        if isinstance(image, dict):
            lines.append(str(image.get("caption") or ""))
        lines += str(slide.get("notes") or "").splitlines()
        for line in lines:
            for found in _CLAIM.finditer(line):
                out.append(Claim(index, found.group(0), _canon(found.group(1)),
                                 _unit(found.group(2)), line))
    return out


def source_numbers(texts: list[str]) -> set[str]:
    """Every number any source text holds, by value."""
    found: set[str] = set()
    budget = MAX_SOURCE_CHARS
    for text in texts:
        if budget <= 0:
            break
        chunk = text[:budget]
        budget -= len(chunk)
        found.update(_canon(m.group(1)) for m in _ANY.finditer(chunk))
    return found


def source_ranges(texts: list[str]) -> list[tuple[str, str, str, str]]:
    """`(low, high, unit, excerpt)` of each range the sources give with a unit."""
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    budget = MAX_SOURCE_CHARS
    for text in texts:
        if budget <= 0:
            break
        chunk = text[:budget]
        budget -= len(chunk)
        for found in _RANGE.finditer(chunk):
            unit = _unit(found.group(4) or found.group(2) or "")
            low, high = _canon(found.group(1)), _canon(found.group(3))
            try:
                if not Decimal(low) < Decimal(high):
                    continue
            except InvalidOperation:
                continue
            key = (low, high, unit)
            if key not in seen:
                seen.add(key)
                out.append((low, high, unit, found.group(0)))
    return out


@dataclass
class Findings:
    unsourced: list[Claim]
    narrowed: list[tuple[Claim, tuple[str, str, str, str]]]

    def __bool__(self) -> bool:
        return bool(self.unsourced or self.narrowed)

    def lines(self) -> list[str]:
        out = []
        if self.narrowed:
            out.append("자료는 범위로 쓴 수치를 한쪽 끝만 적었습니다(범위를 그대로 쓰세요): "
                       + "; ".join(f"슬라이드 {c.slide} 「{c.text}」 ← 자료 「{r[3]}」"
                                   for c, r in self.narrowed[:6]))
        if self.unsourced:
            out.append("자료에서 찾지 못한 수치(자료의 수치만 쓰세요: 없으면 빼거나 "
                       "「[수치]」 빈칸으로 두세요): "
                       + "; ".join(f"슬라이드 {c.slide} 「{c.text}」" for c in self.unsourced[:8]))
        return out


def check(slides: list[dict], sources: list[str]) -> Findings:
    """What the slides' numbers get wrong against `sources`; empty findings when the sources
    are too thin to judge (a deck made from nothing is the writer's own words)."""
    made = claims(slides)
    if not made or sum(len(s) for s in sources) < 500:
        return Findings([], [])
    known = source_numbers(sources)
    ranges = source_ranges(sources)
    unsourced: list[Claim] = []
    narrowed: list[tuple[Claim, tuple[str, str, str, str]]] = []
    flagged: set[tuple[int, str]] = set()
    for claim in made:
        if claim.number not in known:
            if (claim.slide, claim.text) not in flagged:
                flagged.add((claim.slide, claim.text))
                unsourced.append(claim)
            continue
        at = claim.line.find(claim.text)
        before = claim.line[max(0, at - 14):at]
        slide_numbers = {c.number for c in made if c.slide == claim.slide and c.line == claim.line}
        for low, high, unit, excerpt in ranges:
            if unit != claim.unit or claim.number not in (low, high):
                continue
            other = high if claim.number == low else low
            if other in slide_numbers or other in claim.line:
                continue
            # the wording that makes one end the whole claim: 「최대 20.4%」 when 20.4 is the
            # low end, 「최소 38.2%」 when it is the high end, 「약 …」 for either
            wrong = (
                (_UPPER.search(before) and claim.number == low)
                or (_LOWER.search(before) and claim.number == high)
                or _ABOUT.search(before)
            )
            if wrong and (claim.slide, claim.text) not in flagged:
                flagged.add((claim.slide, claim.text))
                narrowed.append((claim, (low, high, unit, excerpt)))
                break
    return Findings(unsourced, narrowed)
