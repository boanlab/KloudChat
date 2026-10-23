"""Bracket placeholders (「[담당자]」): blanks for the person to fill, never invented."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

#: Hangul 1–10 chars; not links, citations, check boxes, figure marks, 「[참고]」 labels
PLACEHOLDER = re.compile(
    r"(?<!\[)\[(?!(?:미정|참고|붙임|별첨|첨부|예시|주의|요약|결론|그림|표|공지|안내)\]|여기에|(?:그림|표|사진|도표|도식)\s)"
    r"([가-힣](?:[가-힣 ·]{0,8}[가-힣])?)\](?!\(|\[|\])"
)

#: header word -> blank name; first match wins
_FIELD_NAMES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"담당|책임|owner", re.I), "담당자"),
    (re.compile(r"기한|마감|완료일|납기|due|deadline", re.I), "기한"),
    (re.compile(r"단가"), "단가"),
    (re.compile(r"수량"), "수량"),
    (re.compile(r"금액|비용|예산|가격|원\)?$"), "금액"),
    (re.compile(r"일시|일자|날짜|시점|date", re.I), "일자"),
    (re.compile(r"장소"), "장소"),
    (re.compile(r"연락처|전화"), "연락처"),
)
#: short Hangul head label kept as its own blank
_OWN_NAME = re.compile(r"[가-힣](?:[가-힣 ·]{0,8}[가-힣])?")
#: an English head field's blank, one to three words: 「[Location]」, 「[Prepared by]」
EN_HEAD_BLANK = re.compile(r"\[[A-Z][A-Za-z]{1,15}(?: [A-Za-z]{2,12}){0,2}\]")


def blank(name: str) -> str:
    """「[name]」 for a field name: 「보고자(부서)」 → 「[보고자·부서]」, 「Location」 →
    「[Location]」."""
    text = re.sub(r"\s{0,4}\(([^()]{1,20})\)", r"·\1", " ".join(str(name or "").split()))
    text = text.strip(" ·")
    if _OWN_NAME.fullmatch(text or "") or EN_HEAD_BLANK.fullmatch(f"[{text}]"):
        return f"[{text}]"
    return "[내용]"


def for_header(header: str, default: str = "내용") -> str:
    """Blank for a table column: 「담당」 → 「[담당자]」."""
    for pattern, name in _FIELD_NAMES:
        if pattern.search(header or ""):
            return f"[{name}]"
    own = re.sub(r"\([^()]{0,20}\)|[*_`]", "", header or "").strip()
    return f"[{own}]" if _OWN_NAME.fullmatch(own) else f"[{default}]"


def is_blank(text: str) -> bool:
    """Whether a value is only a placeholder; an English head field's own blank counts too."""
    text = str(text or "").strip(" *_`")
    return bool(PLACEHOLDER.fullmatch(text) or EN_HEAD_BLANK.fullmatch(text))


def found(texts: Iterable[str]) -> Counter[str]:
    """Placeholder counts in texts."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(f"[{m.group(1)}]" for m in PLACEHOLDER.finditer(text or ""))
    return counts


def head_blanks(block: dict | None) -> list[str]:
    """Placeholders for empty head fields."""
    out = []
    for pair in (block or {}).get("fields") or []:
        if isinstance(pair, (list, tuple)) and pair and str(pair[0] or "").strip():
            value = str(pair[1] if len(pair) > 1 else "").strip()
            if not value:
                out.append(blank(str(pair[0])))
            elif is_blank(value):
                out.append(value)
    return out


def summary(texts: Iterable[str], block: dict | None = None) -> tuple[int, str]:
    """(n, 「[담당자] 2 · [기한] 1」) summary of blanks left."""
    counts = found(texts)
    counts.update(head_blanks(block))
    total = sum(counts.values())
    shown = " · ".join(f"{name} {n}" if n > 1 else name for name, n in counts.most_common(12))
    return total, shown


#: a blank standing for a date: 「[회의 일자]」, 「[기한]」
_DATE_BLANK = re.compile(r"\[(?:[가-힣]{1,4} ?)?(?:일자|일시|날짜|기한|마감일?)\]")
#: a written date (weekday, time) ending right before the blank
_DATE_BEFORE = re.compile(
    r"(?:\d{1,2} ?일|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.]\d{1,2}\.?)"
    r"(?: ?\([월화수목금토일][^()\n]{0,24}\))?"
    r"(?: ?(?:오전|오후)? ?\d{1,2}(?:시|:\d{2})(?: ?\d{1,2}분)?)?"
    r"[ \t]{1,3}$")
#: a written date starting right after it
_DATE_AFTER = re.compile(
    r"^[ \t]{1,3}(?:(?:20\d{2} ?년 ?)?\d{1,2} ?월 ?\d{1,2} ?일"
    r"|20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2})")


def drop_beside_values(text: str) -> str:
    """A date blank right next to the date it stands for taken out: 「일시: 2026-10-14(수)
    [회의 일자]」 → 「일시: 2026-10-14(수)」."""
    if "[" not in (text or ""):
        return text
    out, at = [], 0
    for m in _DATE_BLANK.finditer(text):
        before, after = text[max(at, m.start() - 60):m.start()], text[m.end():m.end() + 40]
        if _DATE_BEFORE.search(before):
            out.append(text[at:m.start()].rstrip(" \t"))
            at = m.end()
        elif _DATE_AFTER.match(after):
            out.append(text[at:m.start()])
            at = m.end() + (len(after) - len(after.lstrip(" \t")))
    out.append(text[at:])
    return "".join(out)


__all__ = ["EN_HEAD_BLANK", "PLACEHOLDER", "blank", "drop_beside_values", "for_header", "found",
           "head_blanks", "is_blank", "summary"]
