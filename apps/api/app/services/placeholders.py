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


def blank(name: str) -> str:
    """「[name]」 for a field name: 「보고자(부서)」 → 「[보고자·부서]」."""
    text = re.sub(r"\s{0,4}\(([^()]{1,20})\)", r"·\1", " ".join(str(name or "").split()))
    text = text.strip(" ·")
    return f"[{text}]" if _OWN_NAME.fullmatch(text or "") else "[내용]"


def for_header(header: str, default: str = "내용") -> str:
    """Blank for a table column: 「담당」 → 「[담당자]」."""
    for pattern, name in _FIELD_NAMES:
        if pattern.search(header or ""):
            return f"[{name}]"
    own = re.sub(r"\([^()]{0,20}\)|[*_`]", "", header or "").strip()
    return f"[{own}]" if _OWN_NAME.fullmatch(own) else f"[{default}]"


def is_blank(text: str) -> bool:
    """Whether a value is only a placeholder."""
    return bool(PLACEHOLDER.fullmatch(str(text or "").strip(" *_`")))


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


__all__ = ["PLACEHOLDER", "blank", "for_header", "found", "head_blanks", "is_blank", "summary"]
