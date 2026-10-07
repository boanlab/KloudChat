"""Checks on a document plan (headings with a layout each) before anything is written."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from itertools import groupby

#: Longest run of one layout a plan may contain.
MAX_RUN = 3

#: Distinct body layouts a plan should use, capped by how many exist.
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


#: A count that follows one of these counts figures, not the document (「표 한 장」).
_FIGURE_WORDS = (["표"], ["그림"], ["도표"], ["차트"], ["사진"], ["도식"], ["그래프"])


def requested_range(
    request: str, units: tuple[str, ...], *, maximum: int
) -> tuple[int, int] | None:
    """A stated range of the total (「8~10장」) from the instruction only; None without one.

    An exact total (`requested_count`) takes precedence."""
    from app.services.context import instruction_part

    text = " ".join(instruction_part(request or "").split())
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
    return None


def requested_count(request: str, units: tuple[str, ...], *, maximum: int) -> int | None:
    """An explicit total, not a page reference, range or count of just the body."""
    # A count inside pasted material (「GPU는 A100 1장」 in a draft) is not the document's.
    from app.services.context import instruction_part

    request = instruction_part(request)
    # Normalize once so spacing and each candidate's context have bounded work.
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
            ("이상", "이하", "내외", "정도", "~", "–", "-")
        ):
            continue
        if prefix.endswith(("제", "본문", "본론", "내용")):
            continue
        if prefix.split()[-1:] in _FIGURE_WORDS:
            # 「표 한 장」 counts a table, not the document.
            continue
        after = request[match.end() : match.end() + 8]
        if re.match(r"\s{0,8}(?:으로|에)\s{0,8}(?:합|묶|병합|넣|정리|모아|담)", after):
            # 「… 한 장으로 합치고」 merges parts; it is not the total.
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
            # 「총 N장」 states the total outright; the last such statement wins, as a
            # revision note follows the original.
            authoritative.append(value)
            continue
        if value == 1 and suffix.startswith("으로"):
            one_into = True
        found.add(value)
    if authoritative:
        return authoritative[-1]
    if len(found) > 1 and 1 in found and one_into:
        # A 「한 장으로」 beside another count merges parts; it is not the total.
        found.discard(1)
    return next(iter(found)) if len(found) == 1 else None


def count(usage: dict[str, int], spent: dict[str, int], *, planned_apart: bool) -> None:
    """Adds a planning call's tokens to the outline or writer half of a turn's usage."""
    prefix = "outline" if planned_apart else ""
    usage[f"{prefix}InputTokens" if prefix else "inputTokens"] += spent["inputTokens"]
    usage[f"{prefix}OutputTokens" if prefix else "outputTokens"] += spent["outputTokens"]


def flat_layouts(blocks: Sequence[dict], choices: Sequence[str]) -> list[str]:
    """Layouts to ask for more of, or `[]` when the plan is varied enough.

    The first block is the cover and is not a choice.
    """
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
    # Every layout appears but one dominates: name the under-used ones.
    most = max(counted.values())
    return [layout for layout in options if counted[layout] < most]


__all__ = ["MAX_RUN", "MIN_DISTINCT", "count", "flat_layouts"]
