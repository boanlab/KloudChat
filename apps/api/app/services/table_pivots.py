"""Figures a data table in the evidence works out: totals and group sums of its columns.

A person who attaches rows (CSV, TSV, a pipe table) asks for sums by region, month or
product, shares of the total and gaps between periods. Each such figure is one aggregation of
the given rows, so the checks read it as given (`value_guard.Evidence`, `percent_basis`): a
correct 「대구 노트북 순매출 24,645,000원」 is not marked, a wrong regional sum still is.

What is aggregated, per table: each numeric column, the product of two or three of them, and
that product with a rate column (a header naming 「%」, 「율」 or 「률」) as 1 − p/100 and as
p/100; grouped by each label column, the month of a date column, a numeric column of a few
values (a discount rate), whether a rate is above zero, and by two such keys at once; with
months also in runs of two to six (a quarter, 「10~11월」). For each group: its sum, row
count and mean; the gaps between groups of one key; a group's share of the total and the
total less the group. No model call; pure arithmetic of the given cells."""

from __future__ import annotations

import itertools
import re

#: a cell holding one figure: 「1,290,000」, 「10」, 「12.5%」, 「89,000원」
_FIGURE = re.compile(r"^[-+−]?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|원|개|건|명|대)?$")
#: a cell holding a day: 「2026-07-01」, 「2026.7.1」, 「2026/07/01」
_DAY = re.compile(r"^((?:19|20)\d{2})[-./]\s?(\d{1,2})[-./]\s?(\d{1,2})\.?$")
#: a total row of the table itself: 「합계」, 「소계」, 「Total」
_TOTAL = re.compile(r"^(?:합계|총계|소계|총합|계|total|sum)$", re.I)
#: a header naming a rate: 「할인율(%)」, 「수수료율」, 「discount %」
_RATE_HEAD = re.compile(r"%|율|률|rate|pct|percent", re.I)

#: rows read per table at most; tables read per text at most
_MAX_ROWS = 2000
_MAX_TABLES = 6
#: numeric columns combined at most; a key with more values is no group (an id)
_MAX_NUMERIC = 6
_MAX_GROUPS = 40
_MAX_VALUES = 60000


def _cells(line: str, sep: str) -> list[str]:
    if sep == "|":
        return [c.strip(" *") for c in line.strip().strip("|").split("|")]
    return [c.strip().strip('"') for c in line.split(sep)]


def _sep(line: str) -> str:
    """The field break of a data row: a tab, a bar or a comma outside a figure."""
    if line.count("\t") >= 2:
        return "\t"
    if line.count("|") >= 2:
        return "|"
    # a CSV comma, not a thousands one (「1,290,000」)
    bare = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", line)
    return "," if bare.count(",") >= 2 else ""


def tables(text: str) -> list[list[list[str]]]:
    """Each data table of `text` as rows of cells, its header first: runs of three or more
    lines with one field break and one width."""
    out: list[list[list[str]]] = []
    run: list[list[str]] = []
    sep, width = "", 0

    def close() -> None:
        if len(run) >= 3 and len(out) < _MAX_TABLES:
            out.append(run[:_MAX_ROWS + 1])

    for line in (text or "").split("\n"):
        stripped = line.strip()
        if line.count("\t") >= 2:
            # a TSV row keeps its empty last cells (「97.8\t」 under 「비고」): stripped, the
            # row reads one cell short and the table breaks there
            stripped = line.strip(" \r")
        if re.fullmatch(r"\|?[\s:\-|]+\|?", stripped) and "-" in stripped:
            continue  # a markdown rule line
        s = _sep(stripped) if stripped else ""
        cells = _cells(stripped, s) if s else []
        if s and s == sep and len(cells) == width:
            run.append(cells)
            continue
        close()
        run, sep, width = ([cells], s, len(cells)) if s and len(cells) >= 3 else ([], "", 0)
    close()
    return out


def _number(cell: str) -> float | None:
    if not _FIGURE.match(cell.strip()):
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", cell.replace("−", "-")))
    except ValueError:
        return None


def _columns(table: list[list[str]]):
    """(header, numeric columns, rate columns, key columns) of a table; key columns as
    (name, value per row)."""
    head, rows = table[0], table[1:]
    if all(_number(c) is not None for c in head):
        # no header: the first line is data
        head, rows = [f"c{k}" for k in range(len(head))], table
    # a total row restates the others: summed with them it doubles every figure
    rows = [r for r in rows if not any(_TOTAL.match(c.strip(" *")) for c in r)]
    numeric: dict[int, list[float]] = {}
    keys: list[tuple[str, list[str]]] = []
    for k, name in enumerate(head):
        column = [r[k] for r in rows]
        values = [_number(c) for c in column]
        days = [_DAY.match(c.strip()) for c in column]
        if all(d is not None for d in days):
            keys.append((f"{name}:월", [f"{int(d.group(2))}" for d in days]))  # type: ignore[union-attr]
            continue
        if all(v is not None for v in values):
            numeric[k] = values  # type: ignore[assignment]
            if 1 < len(set(values)) <= 6:
                keys.append((name, [f"{v:g}" for v in values]))  # type: ignore[str-format]
            continue
        distinct = set(column)
        if 1 < len(distinct) <= min(_MAX_GROUPS, max(2, len(rows) - 1)):
            keys.append((name, column))
    rates = {k for k in numeric if _RATE_HEAD.search(head[k])
             and all(0 <= v <= 100 for v in numeric[k])}
    return head, rows, numeric, rates, keys


def _measures(numeric: dict[int, list[float]], rates: set[int]) -> list[list[float]]:
    """Per-row figures to sum: each column, products of two or three, with each rate as
    1 − p/100 and as p/100."""
    plain = [k for k in numeric if k not in rates][:_MAX_NUMERIC]
    out: list[list[float]] = [numeric[k] for k in plain]
    products: list[list[float]] = []
    for size in (2, 3):
        for combo in itertools.combinations(plain, size):
            row = [1.0] * len(numeric[combo[0]])
            for k in combo:
                row = [a * b for a, b in zip(row, numeric[k], strict=True)]
            products.append(row)
    out += products
    for r in rates:
        for base in [*(numeric[k] for k in plain), *products]:
            out.append([b * (1 - p / 100) for b, p in zip(base, numeric[r], strict=True)])
            out.append([b * p / 100 for b, p in zip(base, numeric[r], strict=True)])
    return out


def _windows(values: list[str]) -> dict[str, list[int]]:
    """Month keys in runs of two to six consecutive months (「7~9월」), as extra groups."""
    months = sorted({int(v) for v in values if v.isdigit()})
    out: dict[str, list[int]] = {}
    for size in range(2, 7):
        for k in range(len(months) - size + 1):
            run = months[k:k + size]
            if run[-1] - run[0] == size - 1:
                out[f"{run[0]}~{run[-1]}"] = run
    return out


def aggregates(text: str) -> tuple[list[float], list[float]]:
    """(figures, percentages) the data tables of `text` work out."""
    figures: set[float] = set()
    shares: set[float] = set()
    for table in tables(text):
        try:
            _, rows, numeric, rates, keys = _columns(table)
        except (IndexError, ValueError):
            continue
        if not numeric or len(rows) < 2:
            continue
        measures = _measures(numeric, rates)
        plain = sum(1 for k in numeric if k not in rates)
        n = len(rows)
        groupings: list[dict[str, list[int]]] = []
        for name, column in keys:
            groups: dict[str, list[int]] = {}
            for i, v in enumerate(column):
                groups.setdefault(v, []).append(i)
            if name.endswith(":월"):
                for label, run in _windows(column).items():
                    groups[label] = [i for i, v in enumerate(column)
                                     if v.isdigit() and int(v) in run]
            groupings.append(groups)
        for r in rates:
            groupings.append({"할인>0": [i for i in range(n) if numeric[r][i] > 0]})
        if n <= 500:
            for a, b in itertools.combinations(range(len(groupings)), 2):
                pair: dict[str, list[int]] = {}
                for ka, ia in groupings[a].items():
                    sa = set(ia)
                    for kb, ib in groupings[b].items():
                        both = [i for i in ib if i in sa]
                        if both:
                            pair[f"{ka}|{kb}"] = both
                groupings.append(pair)
        figures.add(float(n))
        for groups in groupings:
            figures.update(float(len(ix)) for ix in groups.values())
        for m, row in enumerate(measures):
            worked: set[float] = set()
            total = sum(row)
            worked.update((total, total / n))
            for groups in groupings:
                sums = []
                for label, ix in groups.items():
                    s = sum(row[i] for i in ix)
                    sums.append(s)
                    worked.update((s, s / len(ix), total - s))
                    if "~" in label:
                        # a run of months: its monthly mean
                        a, b = (int(x) for x in label.split("|")[0].split("~"))
                        worked.add(s / (b - a + 1))
                    if total:
                        shares.add(s / total * 100)
                if len(sums) <= _MAX_GROUPS:
                    for x, y in itertools.combinations(sums, 2):
                        worked.add(abs(x - y))
                        if x and y:
                            shares.update((y / x * 100, x / y * 100, abs(y - x) / x * 100,
                                           abs(y - x) / y * 100))
            # a column's own sums stand at any size (「노트북 34대」); a worked mean, product or
            # gap only from 100 up, where it cannot stand in for any small count by chance
            figures.update(v for v in worked if m < plain and v == int(v) or abs(v) >= 100)
            if len(figures) > _MAX_VALUES:
                break
    return sorted(v for v in figures if v), sorted(shares)


__all__ = ["aggregates", "tables"]
