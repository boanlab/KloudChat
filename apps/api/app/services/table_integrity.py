"""Table rows kept whole: header-only tables, budget items the material lists but the table
lacks, and rows that do not add up to their 합계 row — found, and mended where code can."""

from __future__ import annotations

import re

from app.services import captions, units

_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_CAPTION = re.compile(r"^\s*(?:\*\*)?\s*" + captions.TABLE + r"\s{0,2}(?:\d{1,3}\s{0,2}[.:]|[:：])")
_POINTER = re.compile(r"^\s*(?:아래|다음)\s{0,2}표(?:는|에서|에)")
_TOTAL = re.compile(r"^(?:합계|총계|총액|총\s?예산|계)$")
#: 「강사비 4,200만」 at the end of a 「/」-separated piece
_ITEM = re.compile(
    r"(?:^|[:：,]\s{0,4})(?P<label>[^:：/,]{1,30}?)\s{1,2}"
    r"(?P<num>\d[\d,]{0,15}(?:\.\d{1,6})?)\s?(?P<scale>억|천만|만)\s?원?\s*$"
)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    return re.sub(r"[*_`]", "", cell).strip()


def _key(label: str) -> str:
    return re.sub(r"\([^)]{0,40}\)|[\s·ㆍ,./()\-]", "", _plain(label))


_AMOUNT = re.compile(
    r"-?\d[\d,]{0,15}(?:\.\d{1,6})?(?:\s?(?:억|천만|만)(?:\s?\d[\d,]{0,15}\s?만)?)?")


def _value(cell: str) -> float | None:
    text = re.sub(r"\s?(?:원|%)$", "", _plain(cell)).strip()
    if not _AMOUNT.fullmatch(text):
        return None
    try:
        return units._ko_number(text)
    except ValueError:
        return None


def material_items(material: str) -> list[tuple[str, str]]:
    """`(label, digits)` of a 「/」-separated amount list (3+ items) in the material."""
    out: list[tuple[str, str]] = []
    for line in (material or "").split("\n"):
        if line.count("/") < 2:
            continue
        found = []
        for piece in line.split("/"):
            m = _ITEM.search(piece.strip())
            if m:
                label = re.sub(r"^[-*•·\s①-⑳]+", "", m.group("label")).strip()
                if label:
                    found.append((label, m.group("num")))
        if len(found) >= 3:
            out.extend(found)
    return out


def _tables(lines: list[str]) -> list[tuple[int, int]]:
    """`(header index, end)` of each pipe table (header + rule)."""
    out, i, fenced = [], 0, False
    while i < len(lines):
        if lines[i].strip().startswith("```"):
            fenced = not fenced
        if (not fenced and lines[i].strip().startswith("|") and i + 1 < len(lines)
                and _RULE.match(lines[i + 1])):
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            out.append((i, j))
            i = j
            continue
        i += 1
    return out


def _bare_headers(lines: list[str]) -> list[int]:
    """Lone 「a | b | c」 lines: a table header with no rows."""
    out, fenced = [], False

    def row_like(k: int) -> bool:
        if k < 0 or k >= len(lines):
            return False
        text = lines[k].strip()
        return text.startswith("|") or bool(_RULE.match(text)) or text.count(" | ") >= 1

    for i, line in enumerate(lines):
        text = line.strip()
        if text.startswith("```"):
            fenced = not fenced
        if fenced or not text or text.startswith(("|", "#", "!", ">")):
            continue
        cells = [c for c in text.split("|") if c.strip()]
        if text.count(" | ") >= 2 and len(cells) >= 3 and all(len(c.strip()) <= 20 for c in cells) \
                and not row_like(i - 1) and not row_like(i + 1):
            out.append(i)
    return out


def _sum_gaps(lines: list[str], start: int, end: int) -> list[str]:
    header = _cells(lines[start])
    rows = [_cells(line) for line in lines[start + 2:end]]
    totals = [r for r in rows if r and _TOTAL.match(_plain(r[0]))]
    if len(totals) != 1:
        return []
    total = totals[0]
    data = [r for r in rows if r is not total and r and "소계" not in r[0]]
    out = []
    for col in range(1, len(header)):
        values = [_value(r[col]) if col < len(r) else None for r in data]
        stated = _value(total[col]) if col < len(total) else None
        if len(values) < 2 or stated is None or any(v is None for v in values):
            continue
        added = sum(values)
        if abs(added - stated) > max(abs(stated) * 0.005, 0.05):
            shown = f"{added:,.1f}".removesuffix(".0")
            out.append(f"표 「{_plain(header[col])}」 열의 행을 더하면 {shown}인데 "
                       f"「{_plain(total[0])}」 행은 {_plain(total[col])}이다")
    return out


def _amount_column(lines: list[str], start: int, end: int,
                   items: list[tuple[str, str]]) -> tuple[int, dict[str, int]] | None:
    """Amount column and item key → row index, when the table is the material's ledger."""
    keys = {_key(label): digits for label, digits in items}
    matched: dict[str, int] = {}
    column = None
    for k in range(start + 2, end):
        row = _cells(lines[k])
        key = _key(row[0]) if row else ""
        hit = next((ik for ik in keys if ik and key and (ik in key or key in ik)), None)
        if hit is None:
            continue
        matched[hit] = k
        if column is None:
            column = next((c for c in range(1, len(row))
                           if re.search(rf"(?<![\d,]){re.escape(keys[hit])}(?![\d])", row[c])),
                          None)
    if column is None or len(matched) < 2:
        return None
    return column, matched


def problems(body: str, material: str) -> list[str]:
    """Table defects as sentences naming the table, row or column."""
    lines = (body or "").split("\n")
    out = [f"표 머리 「{lines[i].strip()}」만 있고 행이 없다" for i in _bare_headers(lines)]
    items = material_items(material)
    for start, end in _tables(lines):
        if end == start + 2:
            out.append(f"표 머리 「{lines[start].strip()}」만 있고 행이 없다")
            continue
        out += _sum_gaps(lines, start, end)
        ledger = _amount_column(lines, start, end, items) if items else None
        if ledger:
            _, matched = ledger
            for label, digits in items:
                if _key(label) not in matched:
                    out.append(f"자료의 예산 항목 「{label} {digits}」 행이 표에 없다")
    return out


def _added_row(lines: list[str], start: int, end: int, column: int, sample: int,
               sample_digits: str, label: str, digits: str, total: float | None) -> str:
    header = _cells(lines[start])
    row = _cells(lines[sample])
    cells = []
    for col, name in enumerate(header):
        if col == 0:
            cells.append(label)
        elif col == column:
            cells.append(re.sub(rf"(?<![\d,]){re.escape(sample_digits)}(?![\d])", digits,
                                row[col] if col < len(row) else digits))
        elif total and col < len(row) and ("비율" in name or row[col].strip().endswith("%")):
            places = len(row[col].split(".")[1].rstrip("% ")) if "." in row[col] else 0
            share = units._ko_number(digits) / total * 100
            cells.append(f"{share:.{places}f}" + ("%" if row[col].strip().endswith("%") else ""))
        else:
            cells.append(f"[{re.sub(r'[(（][^)）]{0,20}[)）]', '', _plain(name)).strip()}]")
    return "| " + " | ".join(cells) + " |"


def mend(body: str, material: str) -> str:
    """Listed budget items put back from the material; a header with no rows taken out."""
    lines = (body or "").split("\n")
    items = material_items(material)
    drop: set[int] = set()
    inserts: dict[int, list[str]] = {}
    for start, end in _tables(lines):
        if end == start + 2:
            drop |= {start, start + 1}
            continue
        ledger = _amount_column(lines, start, end, items) if items else None
        if not ledger:
            continue
        column, matched = ledger
        keys = {_key(label): digits for label, digits in items}
        sample_key, sample = next(iter(matched.items()))
        rows = list(range(start + 2, end))
        total_at = next((k for k in rows if _TOTAL.match(_plain(_cells(lines[k])[0]))), None)
        total = None
        if total_at is not None:
            cells = _cells(lines[total_at])
            stated = _value(cells[column]) if column < len(cells) else None
            sample_value = _value(_cells(lines[sample])[column])
            # total in the table's own unit: scaled like the sample row
            if stated and sample_value:
                total = stated * units._ko_number(keys[sample_key]) / sample_value
        added = [
            _added_row(lines, start, end, column, sample, keys[sample_key], label, digits, total)
            for label, digits in items if _key(label) not in matched
        ]
        if added:
            inserts[total_at if total_at is not None else end] = added
    for i in _bare_headers(lines):
        drop.add(i)
    for i in sorted(drop):
        # the header's caption and 「아래 표는 …」 lead go with it
        k = i - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and _CAPTION.match(lines[k]):
            drop.add(k)
            k -= 1
            while k >= 0 and not lines[k].strip():
                k -= 1
            if k >= 0 and _POINTER.match(lines[k]):
                drop.add(k)
    if not drop and not inserts:
        return body
    out: list[str] = []
    for i, line in enumerate(lines):
        out.extend(inserts.get(i, []))
        if i not in drop:
            out.append(line)
    out.extend(inserts.get(len(lines), []))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
