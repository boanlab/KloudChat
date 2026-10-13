"""Document plan (heading + layout) checks before writing."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from itertools import groupby

#: max run of one layout
MAX_RUN = 3

#: distinct body layouts wanted, capped by availability
MIN_DISTINCT = 3

_COUNT_WORDS = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "열한": 11,
    "열두": 12,
}


#: counts after these are figures, not the document (「표 한 장」)
_FIGURE_WORDS = (["표"], ["그림"], ["도표"], ["차트"], ["사진"], ["도식"], ["그래프"])


def requested_range(
    request: str, units: tuple[str, ...], *, maximum: int
) -> tuple[int, int] | None:
    """Stated total range (「8~10장」) from the instruction, or None; exact total wins."""
    return _range_in(_instruction_scope(request, units, maximum), units, maximum=maximum)


def _instruction_scope(request: str, units: tuple[str, ...], maximum: int) -> str:
    """Lead paragraph when it states the size (material below ignored), else the instruction."""
    from app.services.context import instruction_part, lead_instruction

    text = request or ""
    tail = ""
    marker = text.find("\n\n덧붙인 조건:")
    if marker >= 0:
        text, tail = text[:marker], text[marker:]
    instruction = instruction_part(text)
    lead = lead_instruction(text)
    if lead != instruction and (
        _count_in(lead, units, maximum=maximum) is not None
        or _range_in(lead, units, maximum=maximum) is not None
    ):
        return lead + tail
    return instruction + tail


def _range_in(request: str, units: tuple[str, ...], *, maximum: int) -> tuple[int, int] | None:
    """Range stated in this text."""
    text = " ".join(request.split())
    unit = "|".join(re.escape(value) for value in units)
    for match in re.finditer(
        rf"(?<![\d.])(\d{{1,2}})\s{{0,2}}[~～–-]\s{{0,2}}(\d{{1,2}})\s{{0,2}}(?:개\s{{0,2}})?(?:{unit})",
        text,
    ):
        prefix = text[max(0, match.start() - 8) : match.start()].rstrip()
        if prefix.endswith(("제", "본문", "본론", "내용")):
            continue
        lo, hi = int(match.group(1)), int(match.group(2))
        if 1 <= lo < hi <= maximum:
            return lo, hi
    # 「내외/안팎/정도」: ±1/5 (min 1)
    for match in re.finditer(
        rf"(?<![\d.~～–-])(\d{{1,2}})\s{{0,2}}(?:개\s{{0,2}})?(?:{unit})", text
    ):
        if not _APPROXIMATE.match(suffix_words(text, match.end())):
            continue
        prefix = text[max(0, match.start() - 8) : match.start()].rstrip()
        if prefix.endswith(("제", "본문", "본론", "내용")):
            continue
        about = int(match.group(1))
        spread = max(1, round(about / 5))
        lo, hi = max(1, about - spread), min(maximum, about + spread)
        if lo < hi:
            return lo, hi
    return None


#: approximate-count qualifiers
_APPROXIMATE = re.compile(r"(?:으로|로)?\s{0,2}(?:내외|안팎|정도|쯤|가량|남짓|전후|언저리)")


def suffix_words(text: str, end: int) -> str:
    """Few chars after a count (its qualifier)."""
    return text[end : end + 8].lstrip()


def requested_count(request: str, units: tuple[str, ...], *, maximum: int) -> int | None:
    """Explicit total; not a page ref, range, or body-only count."""
    # counts inside pasted material ignored
    return _count_in(_instruction_scope(request, units, maximum), units, maximum=maximum)


#: model/product token before a count (「A100 1장」, 「RTX 4090 2장」)
_PRODUCT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9]*|[A-Za-z]{2,}[ -]?\d+[A-Za-z0-9]*)$"
)


def _count_in(request: str, units: tuple[str, ...], *, maximum: int) -> int | None:
    """Explicit total stated in this text."""
    # normalized once to bound work
    request = " ".join(request.split())
    words = "|".join(sorted(_COUNT_WORDS, key=len, reverse=True))
    unit = "|".join(re.escape(value) for value in units)
    pattern = rf"(?<![\d.+~제-])(\d{{1,3}}|(?<![가-힣])(?:{words})) ?(?:개 ?)?(?:{unit})"
    found: set[int] = set()
    authoritative: list[int] = []
    one_into = False
    for match in re.finditer(pattern, request):
        prefix = request[max(0, match.start() - 8) : match.start()].rstrip()
        suffix = request[match.end() : match.end() + 4]
        if prefix.endswith(("~", "–", "-")) or suffix.lstrip().startswith(
            ("이상", "이하", "~", "–", "-")
        ) or _APPROXIMATE.match(suffix_words(request, match.end())):
            # 「10장 내외」 is a range (requested_range)
            continue
        if prefix.endswith(("제", "본문", "본론", "내용")):
            continue
        if prefix.split()[-1:] in _FIGURE_WORDS:
            # 「표 한 장」 counts a table
            continue
        if _PRODUCT.search(request[max(0, match.start() - 16) : match.start()].rstrip()):
            # 「A100 1장」 counts hardware
            continue
        after = request[match.end() : match.end() + 8]
        if re.match(r"\s{0,8}(?:으로|에)\s{0,8}(?:합|묶|병합|넣|정리|모아|담)", after):
            # 「한 장으로 합치고」 is a merge, not the total
            continue
        if re.match(r"[A-Za-z가-힣]", suffix) and not suffix.startswith(
            ("으로", "로", "짜리", "만", "을", "를", "은", "는", "에", "이", "가")
        ):
            continue
        raw = match.group(1)
        value = int(raw) if raw.isdigit() else _COUNT_WORDS[raw]
        if not value:
            continue
        value = min(value, maximum)
        if prefix.endswith(("총", "전체", "표지 포함", "표지포함", "장수는", "장수")):
            # 「총 N장」 is explicit; last one wins (revisions follow)
            authoritative.append(value)
            continue
        if value == 1 and suffix.startswith("으로"):
            one_into = True
        found.add(value)
    if authoritative:
        return authoritative[-1]
    if len(found) > 1 and 1 in found and one_into:
        # 「한 장으로」 beside another count: merge, not total
        found.discard(1)
    return next(iter(found)) if len(found) == 1 else None


#: an outline longer than this is worth a look before writing
_MANY_SECTIONS = 10
_MANY_SLIDES = 16
#: shorter requests without material leave the scope open (「AI 동향 보고서 써 줘」)
_SPECIFIC_CHARS = 150


def nothing_to_approve(plan: dict | None, request: str, *, has_material: bool = False) -> bool:
    """Whether an offered plan has no real decision in it, so it is written straight away.

    A card stays for pictures to pick (credits), a form for missing facts (`frame`), an
    unsearched topic (`unverified`), a long outline, a slide count off the asked one, or a
    short request with no material behind it.
    """
    plan = plan or {}
    if plan.get("figures") or plan.get("frame") or plan.get("unverified"):
        return False
    slides = plan.get("slides")
    parts = list(slides if slides is not None else plan.get("sections") or [])
    if not parts:
        return False
    if len(parts) == 1:
        return True
    if len(parts) > (_MANY_SLIDES if slides is not None else _MANY_SECTIONS):
        return False
    if slides is not None:
        units = ("장", "슬라이드")
        if (wanted := requested_count(request, units, maximum=60)) and len(parts) != wanted:
            return False
        if (span := requested_range(request, units, maximum=60)) and not (
                span[0] <= len(parts) <= span[1]):
            return False
    return has_material or len((request or "").strip()) >= _SPECIFIC_CHARS


def count(usage: dict[str, int], spent: dict[str, int], *, planned_apart: bool) -> None:
    """Add planning-call tokens to outline/writer usage."""
    prefix = "outline" if planned_apart else ""
    usage[f"{prefix}InputTokens" if prefix else "inputTokens"] += spent["inputTokens"]
    usage[f"{prefix}OutputTokens" if prefix else "outputTokens"] += spent["outputTokens"]


def flat_layouts(blocks: Sequence[dict], choices: Sequence[str]) -> list[str]:
    """Under-used layouts to request, or []; first block is the cover."""
    options = list(choices)
    body = [str(block.get("layout") or "") for block in blocks[1:]]
    if len(options) < 2 or len(body) < MAX_RUN:
        return []
    longest = max(len(list(group)) for _, group in groupby(body))
    counted = Counter(layout for layout in body if layout in options)
    if longest < MAX_RUN and len(counted) >= min(MIN_DISTINCT, len(options)):
        return []
    unused = [layout for layout in options if layout not in counted]
    if unused:
        return unused
    # all layouts present but one dominates
    most = max(counted.values())
    return [layout for layout in options if counted[layout] < most]


__all__ = ["MAX_RUN", "MIN_DISTINCT", "count", "flat_layouts"]
