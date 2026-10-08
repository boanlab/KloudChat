"""Bracket placeholders: 「[담당자]」 names what the person still has to fill in.

What the request and material do not give is written as 「[항목]」 — a short Korean noun
for what goes there — and never made up. Checks read one as a valid blank, not a defect,
and count them so the reader sees what is left to fill.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

#: 「[담당자]」 「[구체적 성과 수치]」: Hangul (spaces and · inside), 1–10 characters. Not a
#: Markdown link 「[text](url)」, a citation 「[1]」, a check box 「[ ]」/「[x]」, a 「[[그림 …]]」
#: mark, or a label a document uses for itself (「[참고]」 「[붙임]」).
PLACEHOLDER = re.compile(
    r"(?<!\[)\[(?!(?:미정|참고|붙임|별첨|첨부|예시|주의|요약|결론|그림|표|공지|안내)\]|여기에)"
    r"([가-힣](?:[가-힣 ·]{0,8}[가-힣])?)\](?!\(|\[|\])"
)

#: Header words and the name their blank takes; the first that matches wins.
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
#: A head label's own name, kept when it is short Hangul: 「작성자」 → 「[작성자]」.
_OWN_NAME = re.compile(r"[가-힣](?:[가-힣 ·]{0,8}[가-힣])?")


def blank(name: str) -> str:
    """「[name]」 for a field's own name: 「보고자(부서)」 → 「[보고자·부서]」."""
    text = re.sub(r"\s{0,4}\(([^()]{1,20})\)", r"·\1", " ".join(str(name or "").split()))
    text = text.strip(" ·")
    return f"[{text}]" if _OWN_NAME.fullmatch(text or "") else "[내용]"


def for_header(header: str, default: str = "내용") -> str:
    """The blank a table column takes: 「담당」 → 「[담당자]」, 「기한」 → 「[기한]」."""
    for pattern, name in _FIELD_NAMES:
        if pattern.search(header or ""):
            return f"[{name}]"
    own = re.sub(r"\([^()]{0,20}\)|[*_`]", "", header or "").strip()
    return f"[{own}]" if _OWN_NAME.fullmatch(own) else f"[{default}]"


def is_blank(text: str) -> bool:
    """Whether a cell or field value is a placeholder and nothing else."""
    return bool(PLACEHOLDER.fullmatch(str(text or "").strip(" *_`")))


def found(texts: Iterable[str]) -> Counter[str]:
    """Each placeholder in `texts` and how often: 「[담당자]」 → 2."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(f"[{m.group(1)}]" for m in PLACEHOLDER.finditer(text or ""))
    return counts


def head_blanks(block: dict | None) -> list[str]:
    """The placeholders an empty head field shows: 「작성자」 left blank → 「[작성자]」."""
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
    """`(n, 「[담당자] 2 · [기한] 1」)` — the blanks left for the person to fill."""
    counts = found(texts)
    counts.update(head_blanks(block))
    total = sum(counts.values())
    shown = " · ".join(f"{name} {n}" if n > 1 else name for name, n in counts.most_common(12))
    return total, shown


__all__ = ["PLACEHOLDER", "blank", "for_header", "found", "head_blanks", "is_blank", "summary"]
