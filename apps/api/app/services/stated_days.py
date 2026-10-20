"""Days a document puts an item on that the material never puts it on.

A memo's 「요청사항: 물류팀 출고 SLA 협의 자리 마련」 asks for a meeting and names no day; a plan
table row 「금요일 | 4분기 목표 초안 작성, 물류팀 출고 SLA 협의(예정)」 reads as a fixed date.
An item may stand on a weekday only when a stretch of the material on that weekday (a 「- 금:」
line, the clause around 「금요일」 or 「(금)」) shares one of its words.
"""

from __future__ import annotations

import re

from app.services import stated_policy

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


def _words(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", text):
        word = _PARTICLE.sub("", word) if re.search(r"[가-힣]", word) else word
        if len(word) >= 2 and word not in _GENERIC and not word.isdigit() \
                and not re.fullmatch(rf"[{_DAYS}]요일", word):
            out.add(word.lower())
    return out


def days_of(material: str) -> dict[str, list[str]]:
    """Stretches of the material per weekday: a day-labelled line whole, else the clause
    around the weekday."""
    out: dict[str, list[str]] = {}
    for line in (material or "").split("\n"):
        if m := _DAY_LINE.match(line):
            out.setdefault(m.group(1), []).append(line)
        for clause in _split(line):
            for m in _MATERIAL_DAY.finditer(clause):
                out.setdefault(m.group(1) or m.group(2), []).append(clause)
    return out


def _held(item: str, day: str, days: dict[str, list[str]]) -> bool:
    words = _words(item)
    if not words:
        return True
    squeezed = [re.sub(r"\s+", "", c).lower() for c in days.get(day, [])]
    return any(w in c for w in words for c in squeezed)


def untraced(piece: str, material: str) -> list[str]:
    """Items of a sentence or table row put on a weekday the material never gives them."""
    if not piece or _CITES.search(piece) or not (material or "").strip():
        return []
    days = days_of(material)
    out: list[str] = []
    if piece.lstrip().startswith("|"):
        cells = [c.strip() for c in piece.strip().strip("|").split("|")]
        day = next((m.group(1) for c in cells[:1] if (m := _DAY_CELL.match(c))), None)
        rest = [c for c in cells[1:] if c]
        if day is None or not rest:
            return []
        out += [item for item in _split(max(rest, key=len)) if not _held(item, day, days)]
        return out
    for clause in _split(piece):
        for m in _SAID_DAY.finditer(clause):
            if _AFTER_DATE.search(clause[:m.start()]):
                continue
            if not _held(clause[:m.start()] + clause[m.end():], m.group(1), days):
                out.append(clause.rstrip(".!? "))
                break
    return out


def findings(sections: list[dict], material: str, skip_heading: re.Pattern | None = None
             ) -> list[tuple[int, str, list[str]]]:
    """(section, sentence or row, items on an untraced day) over a document."""
    out = []
    for index, sec in enumerate(sections):
        if skip_heading is not None and skip_heading.search(str(sec.get("heading") or "")):
            continue
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if items := untraced(piece, material):
                out.append((index, piece, items))
    return out


def why(items: list[str]) -> str:
    shown = ", ".join(f"「{i}」" for i in items)
    return (f"{shown}: 자료에는 이 일을 이 요일에 한다는 말이 없다(요청·과제뿐이다). 요일을 "
            "지어 붙이지 말고, 표에서는 이 항목을 빼거나 「미정」으로 두고, 문장에서는 요일 없이 "
            "자료가 말한 대로 써라.")


__all__ = ["days_of", "findings", "untraced", "why"]
