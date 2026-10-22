"""Days a document puts an item on that the material never puts it on.

A memo's 「요청사항: 물류팀 출고 SLA 협의 자리 마련」 asks for a meeting and names no day; a plan
table row 「금요일 | 4분기 목표 초안 작성, 물류팀 출고 SLA 협의(예정)」 reads as a fixed date.
An item may stand on a weekday only when a stretch of the material on that weekday (a 「- 금:」
line, the clause around 「금요일」 or 「(금)」) shares one of its words, and a this-week stretch
holds no next-week plan the material lists on no day and words more closely or in full
(「- 화: 한솔마트 재고 클레임」 does not put 「한솔마트 재고 점검 방문」, under 「다음주 계획」,
on a 화요일).
An item the document places in one week (「이번 주 목요일」, a 「다음 주 업무 계획」 section, the
report's own 「10월 3주차」) stands only on a stretch of that week, and, when its own words name
the week, not where the other week's stretch on that day says it more closely (「다음주 계획: …
(목요일 2차 미팅)」 is no 「이번 주 목요일 2차 미팅」). The item a clause dates may be named in the
sentence's other clauses (「CRM 병합은 60% 진행되었고, 완료 시점은 다음 주 수요일입니다」).
"""

from __future__ import annotations

import re

from app.services import stated_policy, weekdays

_DAYS = "월화수목금토일"
#: a weekday in the material: 「금요일」, 「(금)」
_MATERIAL_DAY = re.compile(rf"(?<![가-힣])([{_DAYS}])요일|\(\s?([{_DAYS}])(?:요일)?\s?\)")
#: a memo line under a day label: 「- 금: CRM 정리」
_DAY_LINE = re.compile(rf"^\s*(?:[-*·•]\s*)?([{_DAYS}])(?:요일)?\s*[:：)]", re.M)
#: a weekday in a sentence of the document, not right after a date (「10월 23일 금요일」)
_SAID_DAY = re.compile(rf"(?<![가-힣])([{_DAYS}])요일")
_AFTER_DATE = re.compile(r"(?:\d\s?일|\d[/.]\d{1,2})\s{0,2}\(?\s?$")
#: a table cell that is a weekday and nothing else: 「금요일」, 「다음 주 금」
_DAY_CELL = re.compile(rf"^\s*(?:다음\s?주\s?)?([{_DAYS}])(?:요일)?\s*$")
#: words that say what kind of thing happens, not which one
_GENERIC = frozenset((
    "협의", "작성", "예정", "진행", "완료", "마무리", "처리", "확인", "검토", "일정", "회의",
    "미팅", "계획", "다음", "이번", "주간", "오전", "오후", "업무", "관련", "대한", "위한", "내용",
    "필요", "요청", "준비", "실시", "보고", "공유", "참석", "방문", "추진", "착수", "시작", "자리",
    "마련", "까지", "이후", "이전", "당일", "중간", "최종", "초안", "논의", "점검", "정리", "작업",
    "기한",
))
_PARTICLE = re.compile(r"(?:으로|에서|에게|까지|부터|이며|하며|하고|에는|은|는|이|가|을|를|에|의|"
                       r"과|와|로|도|만)$")
_CITES = re.compile(r"\[\d{1,3}(?:\s?[,\-–]\s?\d{1,3})*\]")
#: a stretch about next week: 「다음주 계획」, 「차주」
_NEXT_WEEK = re.compile(r"다음\s?주|차주|내주|next\s?week", re.I)
_THIS_WEEK = re.compile(r"이번\s?주|금주|this\s?week", re.I)
#: a named week: 「10월 3주차」
_WEEK_LABEL = re.compile(r"\d{1,2}월\s?\d\s?주\s?차")
#: a memo line's label: 「- 다음주 계획:」
_LABEL = re.compile(r"^\s*(?:[-*·•]\s*)?[^:：\n]{0,20}[:：]")
#: a list item, not a heading over a block of lines
_BULLET = re.compile(r"^\s*(?:[-*·•]|\d{1,2}[.)])\s")


def _split(text: str) -> list[str]:
    """`text` at commas and semicolons outside brackets."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([（":
            depth += 1
        elif ch in ")]）" and depth:
            depth -= 1
        elif ch in ",;，·" and not depth:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [p.strip() for p in out if p.strip()]


def _bare(word: str) -> str:
    """A word less its particle, unless that leaves one syllable (「회의」 is no 「회」+「의」)."""
    if not re.search(r"[가-힣]", word):
        return word
    stem = _PARTICLE.sub("", word)
    return stem if len(stem) >= 2 else word


def _words(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", text):
        word = _bare(word)
        if len(word) >= 2 and word not in _GENERIC and not word.isdigit() \
                and not re.fullmatch(rf"[{_DAYS}]요일", word):
            out.add(word.lower())
    return out


def week_of(text: str, material: str = "") -> str | None:
    """"this" or "next" for a text that says which week (「이번 주」, 「다음주」, a 「10월 3주차」
    the material says on a line of that week), else None."""
    nxt, this = bool(_NEXT_WEEK.search(text or "")), bool(_THIS_WEEK.search(text or ""))
    if nxt != this:
        return "next" if nxt else "this"
    if nxt or not material:
        return None
    for m in _WEEK_LABEL.finditer(text or ""):
        label = re.sub(r"\s+", "", m.group(0))
        for line in material.split("\n"):
            if label in re.sub(r"\s+", "", line) and (week := week_of(line)):
                return week
    return None


def _stretches(material: str) -> dict[str, list[tuple[str, str | None]]]:
    """(stretch, week) of the material per weekday: a day-labelled line whole, else the clause
    around the weekday; the week the stretch, its line or the heading over its block says."""
    out: dict[str, list[tuple[str, str | None]]] = {}
    block = None
    for line in (material or "").split("\n"):
        day_line = _DAY_LINE.match(line)
        if line.strip() and not day_line and not _BULLET.match(line):
            block = week_of(line)
        line_week = week_of(line) or block
        if day_line:
            out.setdefault(day_line.group(1), []).append((line, line_week))
        for clause in _split(line):
            for m in _MATERIAL_DAY.finditer(clause):
                out.setdefault(m.group(1) or m.group(2), []).append(
                    (clause, week_of(clause) or line_week))
    return out


def days_of(material: str) -> dict[str, list[str]]:
    """Stretches of the material per weekday: a day-labelled line whole, else the clause
    around the weekday."""
    return {day: [c for c, _ in found] for day, found in _stretches(material).items()}


def _all_words(text: str) -> set[str]:
    """`_words` with the generic ones kept: how closely a stretch says the same item."""
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", text):
        word = _bare(word)
        if len(word) >= 2 and not word.isdigit():
            out.add(word.lower())
    return out


def _overlap(words: set[str], stretch: str) -> int:
    squeezed = re.sub(r"\s+", "", stretch).lower()
    return sum(1 for w in words if w in squeezed)


def _next_week_items(material: str) -> list[str]:
    """Clauses of the material's next-week lines that name no weekday: 「다음주 계획: …, 한솔마트
    재고 점검 방문, …」 → 「한솔마트 재고 점검 방문」."""
    return [clause for line in (material or "").split("\n") if _NEXT_WEEK.search(line)
            for clause in _split(line) if not _MATERIAL_DAY.search(clause)
            and not _DAY_LINE.match(clause)]


def _says(item: str, plan: str) -> bool:
    """Whether `item` says every word of a material plan clause (its 「다음주 계획:」 label
    aside)."""
    words = _all_words(_LABEL.sub("", plan))
    return len(words) >= 2 and _overlap(words, item) == len(words)


def _held(item: str, day: str, days: dict[str, list[tuple[str, str | None]]],
          material: str = "", week: str | None = None, own: bool = True) -> bool:
    words = _words(item)
    if not words:
        return True
    hits = [(c, w) for c, w in days.get(day, []) if _overlap(words, c)]
    every = _all_words(item)
    same = [c for c, w in hits if week is None or w in (None, week)]
    other = [c for c, w in hits if c not in same]
    if not same:
        return False
    # every word of that day's stretch in the week, and more (「결제 오류 사후 검토 회의(…)」)
    if any(_says(item, _MATERIAL_DAY.sub(" ", c)) for c in same):
        return True
    best = max(_overlap(every, c) for c in same)
    # the other week's stretch on this day says it more closely: the item moved weeks (a week
    # the item says itself; one a heading gives may hold both weeks' talk of the day)
    if own and other and max(_overlap(every, c) for c in other) > best:
        return False
    if any(w == "next" for c, w in hits if c in same):
        return True
    # this week's 「- 화: 한솔마트 재고 클레임」 holds no next-week plan the material puts on no
    # day and says more closely or in full (「한솔마트 재고 점검 방문」)
    return not any(_overlap(every, c) > best or _says(item, c)
                   for c in _next_week_items(material))


def untraced(piece: str, material: str, context: str = "") -> list[str]:
    """Items of a sentence or table row put on a weekday, or in a week (its own words, else
    `context`: the section heading, a table's caption), the material never gives them."""
    if not piece or _CITES.search(piece) or not (material or "").strip():
        return []
    # an English memo's 「Oct 26 (Mon)」 is a Monday too
    material = weekdays.english_days(material)
    days = _stretches(material)
    around = week_of(context, material)
    out: list[str] = []
    if piece.lstrip().startswith("|"):
        cells = [c.strip() for c in piece.strip().strip("|").split("|")]
        day = next((m.group(1) for c in cells[:1] if (m := _DAY_CELL.match(c))), None)
        rest = [c for c in cells[1:] if c]
        if day is None or not rest:
            return []
        week = week_of(cells[0], material) or around
        out += [item for item in _split(max(rest, key=len))
                if not _held(item, day, days, material, week)]
        return out
    for clause in _split(piece):
        for m in _SAID_DAY.finditer(clause):
            if _AFTER_DATE.search(clause[:m.start()]):
                continue
            said = (week_of(clause[max(0, m.start() - 16):m.start()], material)
                    or week_of(clause, material) or week_of(piece, material))
            week, own = said or around, said is not None
            # the item the clause dates may be named in the sentence's other clauses
            # (「CRM 병합은 60% 진행되었고, 완료 시점은 다음 주 수요일입니다」)
            if not any(_held(text[:at] + text[at + len(m.group(0)):], m.group(1), days, material,
                             week, own)
                       for text, at in ((clause, m.start()),
                                        (piece, piece.find(m.group(0), piece.find(clause))))
                       if at >= 0):
                out.append(clause.rstrip(".!? "))
                break
    return out


def context_of(heading: str, content: str, piece: str) -> str:
    """What places a piece in a week beside its own words: the section heading, and for a table
    row the caption or line over its table."""
    out = [heading or ""]
    if piece.lstrip().startswith("|") and (at := (content or "").find(piece)) >= 0:
        above = [line for line in content[:at].split("\n") if line.strip()]
        while above and above[-1].lstrip().startswith("|"):
            above.pop()
        if above:
            out.append(above[-1])
    return "\n".join(out)


def findings(sections: list[dict], material: str, skip_heading: re.Pattern | None = None
             ) -> list[tuple[int, str, list[str]]]:
    """(section, sentence or row, items on an untraced day or week) over a document."""
    out = []
    for index, sec in enumerate(sections):
        heading = str(sec.get("heading") or "")
        if skip_heading is not None and skip_heading.search(heading):
            continue
        content = str(sec.get("content") or "")
        for piece in stated_policy.pieces(content):
            if items := untraced(piece, material, context_of(heading, content, piece)):
                out.append((index, piece, items))
    return out


def why(items: list[str]) -> str:
    shown = ", ".join(f"「{i}」" for i in items)
    return (f"{shown}: 자료에는 이 일을 이 요일에 한다는 말이 없다(요청·과제뿐이거나 다른 주의 "
            "일이다). 요일을 지어 붙이거나 자료가 다음 주라고 한 일을 이번 주로(또는 그 반대로) "
            "옮기지 말고, 표에서는 이 항목을 빼거나 「미정」으로 두고, 문장에서는 요일 없이 자료가 "
            "말한 대로 써라.")


__all__ = ["context_of", "days_of", "findings", "untraced", "week_of", "why"]
