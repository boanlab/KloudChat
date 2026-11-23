"""Charts drawn from a report's own tables: the code parses the table and draws its values, so no
number is retyped by a model. A table whose first column is a time (years, months, quarters) is a
trend (lines); one whose first column names categories is a comparison (bars). Columns of other
units or other magnitudes get a panel of their own, side by side."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

#: tables charted per report
MAX_CHARTS = 4
#: data rows a chart needs (a table of two rows is a sentence)
MIN_ROWS = 3
MAX_TIME_ROWS, MAX_BAR_ROWS = 24, 12
#: panels (units or magnitudes) one chart holds
MAX_PANELS = 4
MAX_SERIES = 5

_TIME = re.compile(
    r"^(?:\d{4}\s?년?(?:\s?(?:상반기|하반기|[1-4]\s?분기|Q[1-4]|\d{1,2}\s?월))?"
    r"|\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?|\d{1,2}\s?월|\d{1,2}\s?주\s?차?|[1-4]\s?분기|Q[1-4]"
    r"|FY\s?\d{2,4}|'?\d{2}\s?년|(?:상|하)반기|\d{4}\s?[~-]\s?\d{4})$", re.I)
_SKIP_COLUMN = re.compile(
    r"^(?:no\.?|번호|순번|순위|rank|id|연도|년도|year|date|날짜|기간|비고|설명|내용|메모|note)$",
    re.I)
_GENERIC_X = re.compile(r"^(?:항목|구분|분류|내용|이름|명칭|name|item|category)$", re.I)
_TOTAL_ROW = re.compile(r"^(?:합계|총계|총합|소계|전체|평균|계|total|sum|average|avg)$", re.I)
#: a note after a number: 「(예시)」, 「(확인 필요)」, 「(추정)」
_TAG = re.compile(r"\s*[(（][^()（）]{0,14}[)）]")
_NUMBER = re.compile(
    r"^[약~≈]?\s*(?P<n>[+\-−]?\d[\d,]*(?:\.\d+)?)\s*(?P<u>%p|%|[^\d\s,.\-+][^\d]{0,7})?$")
_HEAD_UNIT = re.compile(r"\s*[(（\[]\s*(?:단위\s*[:：]?\s*)?([^()（）\[\]]{1,10})\s*[)）\]]\s*$")
#: words in a section saying its figures are made up
_EXAMPLE_PHRASE = re.compile(
    r"(?:예시|가상|가정|임의|지어낸)\s?(?:으로\s?)?(?:값|수치|데이터|자료)|예시\s?값|지어낸\s?(?:것|수치|값)"
    r"|(?:illustrative|hypothetical|sample|dummy)\s+(?:data|figures|numbers|values)"
    r"|(?:수치|숫자|값|데이터)[^\n.]{0,12}?(?:예시|가상)|(?:예시|가상)로\s?(?:지어|만들)", re.I)
_DECLINED = re.compile(
    r"(?:그래프|차트|그림|도표|chart|graph)s?\s{0,2}(?:은|는|을|를|도)?\s{0,2}"
    r"(?:없이|빼|넣지\s{0,2}마|필요\s{0,2}없|without)|without\s+(?:any\s+)?(?:charts?|graphs?|figures)",
    re.I)


@dataclass(frozen=True, slots=True)
class Series:
    name: str
    values: tuple[float | None, ...]
    #: the numbers as the table wrote them (no note, no unit)
    texts: tuple[str, ...]
    unit: str = ""


@dataclass(slots=True)
class TableChart:
    """A table found in a section and how to draw it."""

    kind: str  # "line" | "bar"
    x_name: str
    labels: tuple[str, ...]
    panels: list[list[Series]]
    #: the table's own Markdown
    table: str
    #: first line of the table in the section, last line (exclusive)
    start: int = 0
    end: int = 0
    example: bool = False
    key: str = ""
    caption: str = field(default="")


def declined(request: str) -> bool:
    return bool(_DECLINED.search(request or ""))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_rule(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells)


def tables_in(content: str) -> list[tuple[int, int, list[list[str]]]]:
    """(first line, line after the last, rows without the rule) of each Markdown table outside
    code fences."""
    lines = (content or "").split("\n")
    out, i, fenced = [], 0, False
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            fenced = not fenced
        if (not fenced and stripped.startswith("|") and i + 1 < len(lines)
                and _is_rule(lines[i + 1])):
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            rows = [_cells(lines[i])] + [_cells(line) for line in lines[i + 2:j]]
            out.append((i, j, rows))
            i = j
            continue
        i += 1
    return out


def parse_cell(raw: str) -> tuple[float, str, str] | None:
    """(value, unit, the number as written) of a numeric cell, or None: 「5.2%(확인 필요) (예시)」
    → 5.2, %; 「1,200억 원」 → 1200, 억 원."""
    text = _TAG.sub("", raw or "").replace("*", "").replace("`", "").strip()
    m = _NUMBER.match(text)
    if not m:
        return None
    written = m.group("n").replace("−", "-").replace(",", "")
    try:
        value = float(written)
    except ValueError:
        return None
    return value, (m.group("u") or "").strip(), written.lstrip("+")


def _split_head(head: str) -> tuple[str, str]:
    """Column name and the unit its header gives: 「불량률 (%)」 → 불량률, %."""
    head = re.sub(r"\*", "", head or "").strip()
    m = _HEAD_UNIT.search(head)
    if m and len(head) > len(m.group(0)):
        return head[:m.start()].strip(), m.group(1).strip()
    return head, ""


def _column(head: str, cells: list[str]) -> Series | None:
    name, head_unit = _split_head(head)
    if not name or _SKIP_COLUMN.match(name):
        return None
    values: list[float | None] = []
    texts: list[str] = []
    units: set[str] = set()
    for cell in cells:
        plain = _TAG.sub("", cell).strip()
        if plain in {"", "-", "–", "—", "N/A", "n/a", "해당 없음", "미정"}:
            values.append(None)
            texts.append("")
            continue
        parsed = parse_cell(cell)
        if parsed is None:
            return None
        value, unit, written = parsed
        values.append(value)
        texts.append(written)
        units.add(unit)
    if len(units - {""}) > 1 or ("" in units and len(units) > 1 and not head_unit):
        return None
    valid = [v for v in values if v is not None]
    if len(valid) < MIN_ROWS or len(valid) * 4 < len(values) * 3:
        return None
    unit = (units - {""}).pop() if units - {""} else head_unit
    return Series(name=name, values=tuple(values), texts=tuple(texts), unit=unit)


def _transposed(rows: list[list[str]]) -> list[list[str]]:
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    return [list(col) for col in zip(*padded, strict=True)]


def _groups(columns: list[Series]) -> list[list[Series]]:
    """Columns that share a panel: one unit and magnitudes within a factor of four; others
    stand apart, in table order."""
    panels: list[list[Series]] = []
    for col in columns:
        top = max(abs(v) for v in col.values if v is not None) or 1.0
        for panel in panels:
            if panel[0].unit != col.unit or len(panel) >= MAX_SERIES:
                continue
            tops = [max(abs(v) for v in s.values if v is not None) or 1.0 for s in panel]
            if max(top, *tops) / max(min(top, *tops), 1e-9) <= 4:
                panel.append(col)
                break
        else:
            panels.append([col])
    return panels


def parse_table(rows: list[list[str]]) -> TableChart | None:
    """The chart a table asks for, or None when it is not numbers over a time or a set of
    categories."""
    if len(rows) < 3 or max(len(r) for r in rows) < 2:
        return None
    first = [r[0] if r else "" for r in rows[1:]]
    if not all(_TIME.match(_TAG.sub("", c).strip()) for c in first):
        head_times = [_TIME.match(_TAG.sub("", c).strip()) for c in rows[0][1:]]
        if len(head_times) >= MIN_ROWS and all(head_times):
            return parse_table(_transposed(rows))
    labels = [_TAG.sub("", c).replace("*", "").strip() for c in first]
    time = all(_TIME.match(c) for c in labels)
    if not time and any(parse_cell(c) is not None for c in labels):
        return None
    if not labels or any(not c for c in labels) or len(set(labels)) != len(labels):
        return None
    keep = list(range(len(labels)))
    if not time:
        keep = [i for i in keep if not _TOTAL_ROW.match(labels[i])]
    if len(keep) < MIN_ROWS or len(keep) > (MAX_TIME_ROWS if time else MAX_BAR_ROWS):
        return None
    body = [rows[1:][i] for i in keep]
    columns = []
    width = max(len(r) for r in rows)
    for j in range(1, width):
        head = rows[0][j] if j < len(rows[0]) else ""
        col = _column(head, [r[j] if j < len(r) else "" for r in body])
        if col is not None:
            columns.append(col)
    if not columns:
        return None
    panels = _groups(columns)[:MAX_PANELS]
    x_name = re.sub(r"\*", "", rows[0][0]).strip()
    return TableChart(kind="line" if time else "bar", x_name=x_name,
                      labels=tuple(labels[i] for i in keep), panels=panels, table="")


def is_example(chart: TableChart, section_text: str, request: str = "") -> bool:
    """Whether the table's numbers say they are made up: a tag in a cell or the table's
    section or the request saying so."""
    if _EXAMPLE_PHRASE.search(section_text or "") or _EXAMPLE_PHRASE.search(request or ""):
        return True
    return bool(re.search(r"\((?:예시|가상|가정|임의)[^()]{0,8}\)", chart.table))


def _caption(chart: TableChart, english: bool) -> str:
    names = [s.name for panel in chart.panels for s in panel]
    shown = "·".join(names[:3]) + (" 등" if len(names) > 3 else "")
    if english:
        text = f"{', '.join(names[:3])} {'trend' if chart.kind == 'line' else 'comparison'}"
        return text + (" (illustrative)" if chart.example else "")
    x = chart.x_name
    by = ""
    if x and not _GENERIC_X.match(x):
        by = (x if x.endswith("별") else x + "별") + " "
    text = f"{by}{shown} {'추이' if chart.kind == 'line' else '비교'}"
    return text + (" (예시)" if chart.example else "")


def _key(chart: TableChart) -> str:
    raw = json.dumps([chart.labels, [[s.name, s.texts] for p in chart.panels for s in p]],
                     ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


#: a picture follows the table within this many blocks: it already has its figure
_PICTURE = re.compile(r"^\s*(?:!\[|```mermaid|```chart|\*그림|\*Figure)")


def has_figure_after(lines: list[str], end: int) -> bool:
    """Whether a figure stands right after the table (a chart drawn before, a writer's figure)."""
    seen = 0
    for line in lines[end:]:
        if not line.strip():
            continue
        if _PICTURE.match(line):
            return True
        seen += 1
        if seen >= 2:
            return False
    return False


def find(content: str, *, request: str = "", english: bool = False, taken: set[str] | None = None,
         ) -> list[TableChart]:
    """Charts for the tables of one section, in order; `taken` holds keys of tables charted
    already (a table is charted once per report) and is added to."""
    taken = taken if taken is not None else set()
    lines = (content or "").split("\n")
    out = []
    for start, end, rows in tables_in(content):
        chart = parse_table(rows)
        if chart is None:
            continue
        chart.table = "\n".join(lines[start:end])
        chart.start, chart.end = start, end
        chart.key = _key(chart)
        if chart.key in taken or has_figure_after(lines, end):
            continue
        chart.example = is_example(chart, content, request)
        chart.caption = _caption(chart, english)
        taken.add(chart.key)
        out.append(chart)
    return out


def insert(content: str, chart: TableChart, markdown: str) -> str:
    """`content` with `markdown` (the figure) right after the chart's table."""
    lines = content.split("\n")
    # the table's lines may have moved: find it again by its text
    block = chart.table.split("\n")
    at = next((i for i in range(len(lines) - len(block) + 1) if lines[i:i + len(block)] == block),
              None)
    if at is None:
        return content
    end = at + len(block)
    return "\n".join([*lines[:end], "", markdown, "", *lines[end:]]).replace("\n\n\n", "\n\n")


#: size in inches of one chart: one panel, two side by side, four in a grid
def _figsize(panels: int) -> tuple[float, float]:
    return {1: (7.6, 4.0), 2: (8.6, 3.8)}.get(panels, (8.6, 6.6))


_CODE = '''import json, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "NanumGothic"
plt.rcParams["axes.unicode_minus"] = False
spec = json.loads(%(spec)s)
PALETTE = ["#1e3a8a", "#0f766e", "#b45309", "#7c3aed", "#64748b"]
INK, SUB, GRID = "#1f2937", "#475569", "#e2e8f0"
labels = spec["labels"]
panels = spec["panels"]
n = len(panels)
cols = 1 if n == 1 else 2
rows = -(-n // cols)
fig, axes = plt.subplots(rows, cols, figsize=tuple(spec["size"]), dpi=200, squeeze=False)
horizontal = spec["kind"] == "bar" and max(len(l) for l in labels) > 8 and n == 1
many = len(labels) > 6
shown = [textwrap.fill(l, 9) for l in labels]
color_at = 0
for k, panel in enumerate(panels):
    ax = axes[k // cols][k %% cols]
    series = panel["series"]
    nums = [v for s in series for v in s["values"] if v is not None]
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or abs(hi) or 1.0
    x = list(range(len(labels)))
    unit = panel["unit"]
    for si, s in enumerate(series):
        color = PALETTE[(color_at + si) %% len(PALETTE)]
        vals = s["values"]
        text = [(t + (unit if unit in ("%%", "%%p") else "")) if t else "" for t in s["texts"]]
        if spec["kind"] == "line":
            pts = [(i, v) for i, v in enumerate(vals) if v is not None]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, marker="o", lw=2.2,
                    ms=6, label=s["name"], zorder=3)
            for i, v in pts:
                ax.annotate(text[i], (i, v), textcoords="offset points",
                            xytext=(0, 8 + 10 * si if len(series) > 1 and si %% 2 else 8),
                            ha="center", fontsize=10, color=INK,
                            bbox=dict(fc="white", ec="none", pad=0.8, alpha=0.85), zorder=4)
        else:
            w = 0.7 / len(series)
            off = (si - (len(series) - 1) / 2) * w
            pos = [i + off for i in x]
            vv = [v if v is not None else 0 for v in vals]
            if horizontal:
                bars = ax.barh(pos, vv, height=w * 0.92, color=color, label=s["name"], zorder=3)
                for b, t, v in zip(bars, text, vals):
                    if v is not None:
                        ax.annotate(t, (v, b.get_y() + b.get_height() / 2),
                                    textcoords="offset points", xytext=(5 if v >= 0 else -5, 0),
                                    va="center", ha="left" if v >= 0 else "right",
                                    fontsize=10, color=INK)
            else:
                bars = ax.bar(pos, vv, width=w * 0.92, color=color, label=s["name"], zorder=3)
                for b, t, v in zip(bars, text, vals):
                    if v is not None:
                        ax.annotate(t, (b.get_x() + b.get_width() / 2, v),
                                    textcoords="offset points", xytext=(0, 4 if v >= 0 else -12),
                                    ha="center", fontsize=10, color=INK)
    color_at += len(series)
    if horizontal:
        ax.set_yticks(x)
        ax.set_yticklabels(shown, fontsize=10.5)
        ax.invert_yaxis()
        ax.set_xlim(min(0, lo - span * 0.1), hi + span * 0.18 if hi > 0 else span * 0.1)
        ax.xaxis.grid(True, color=GRID, zorder=0)
    else:
        ax.set_xticks(x)
        ax.set_xticklabels(shown, fontsize=10.5, rotation=30 if many else 0,
                           ha="right" if many else "center")
        if spec["kind"] == "bar":
            ax.set_ylim(min(0, lo - span * 0.12), hi + span * 0.18 if hi > 0 else span * 0.1)
        else:
            ax.set_ylim(lo - span * 0.22, hi + span * 0.28)
            ax.set_xlim(-0.4, len(labels) - 0.6)
        ax.yaxis.grid(True, color=GRID, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#cbd5e1")
    ax.tick_params(colors=SUB, labelsize=10.5, length=0)
    # No title above the panel: the axis that carries the values says what they are and the unit
    if len(series) == 1:
        value_label = series[0]["name"] + (" (" + unit + ")" if unit else "")
    else:
        value_label = "(" + unit + ")" if unit else ""
        ax.legend(frameon=False, fontsize=10.5, loc="best")
    if horizontal:
        if value_label:
            ax.set_xlabel(value_label, fontsize=10.5, color=SUB)
        if spec["x"]:
            ax.set_ylabel(spec["x"], fontsize=10.5, color=SUB)
    else:
        if value_label:
            ax.set_ylabel(value_label, fontsize=10.5, color=SUB)
        if spec["x"]:
            ax.set_xlabel(spec["x"], fontsize=10.5, color=SUB)
for k in range(n, rows * cols):
    axes[k // cols][k %% cols].axis("off")
fig.tight_layout(w_pad=2.5, h_pad=2.0)
fig.savefig("chart.png", bbox_inches="tight", dpi=200, facecolor="white")
print("OK")
'''


def code_for(chart: TableChart) -> str:
    """matplotlib script drawing exactly the table's values (the same style as the chart
    surface: Nanum Gothic, calm palette, values on the marks, no top and right lines)."""
    spec = {
        "kind": chart.kind,
        "x": "" if _GENERIC_X.match(chart.x_name) else chart.x_name,
        "labels": list(chart.labels),
        "size": _figsize(len(chart.panels)),
        "panels": [{"unit": panel[0].unit,
                    "series": [{"name": s.name, "values": list(s.values), "texts": list(s.texts)}
                               for s in panel]} for panel in chart.panels],
    }
    return _CODE % {"spec": repr(json.dumps(spec, ensure_ascii=False))}


def figure_markdown(chart: TableChart, png_uri: str) -> str:
    """The chart as a report figure with its caption (numbered later with the others)."""
    caption = chart.caption.replace("]", " ")
    return f"![{caption}]({png_uri})\n\n*그림: {caption}*"


__all__ = ["MAX_CHARTS", "TableChart", "code_for", "declined", "figure_markdown", "find",
           "insert", "parse_cell", "parse_table", "tables_in"]
