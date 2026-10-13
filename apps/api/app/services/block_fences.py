"""The app's own block fences (```callout, ```kpi, ```steps, ```cards, ```chart) written as
plain Markdown, for surfaces that show text as it is: the Markdown export and the bot API.
The chat and document views draw these blocks; a Markdown file or an API caller would show
the fence and its `값 | 이름` rows as they stand (r6 A04-T1: 「```callout」 and 「```kpi」 in
the exported report)."""

from __future__ import annotations

import json
import re

#: an opening fence: backticks or tildes, then the info string (its first word is the kind)
_OPEN = re.compile(r"^[ \t]{0,3}(?P<mark>`{3,}|~{3,})[ \t]*(?P<info>[^`\n]*?)[ \t]*$")
#: kinds drawn by the app; ```mermaid is standard Markdown and stays
KINDS = ("callout", "kpi", "steps", "cards", "chart")


def _kind(info: str) -> str:
    word = re.split(r"[\s:{(,;]", info.strip(), maxsplit=1)[0].lower() if info.strip() else ""
    return word if word in KINDS else ""


def _pair(line: str) -> tuple[str, str]:
    left, bar, right = line.partition("|")
    return (left.strip(), right.strip()) if bar else (line.strip(), "")


def _kpi(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        value, label = _pair(line)
        if value and label:
            out.append(f"- **{value}** — {label}")
        elif value:
            out.append(f"- {value}")
    return out


def _steps(lines: list[str]) -> list[str]:
    out = []
    for n, line in enumerate(lines, 1):
        name, detail = _pair(line)
        out.append(f"{n}. **{name}** — {detail}" if name and detail else f"{n}. {name or detail}")
    return out


def _cards(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if out:
                out.append("")
            out.append(f"**{line.lstrip('#').strip()}**")
        else:
            out.append(f"- {line.lstrip('-*').strip()}")
    return out


def _callout(lines: list[str]) -> list[str]:
    title, rest = lines[0].lstrip("#").strip(), lines[1:]
    return [f"> **{title}**", *(f"> {line.lstrip('-*').strip()}" for line in rest)]


def _table(head: list[str], rows: list[list[str]], unit: str = "") -> list[str]:
    width = len(head)
    out = [f"(단위: {unit})", ""] if unit else []
    out.append("| " + " | ".join(head) + " |")
    out.append("|" + "---|" * width)
    for row in rows:
        cells = (row + [""] * width)[:width]
        out.append("| " + " | ".join(cells) + " |")
    return out


def _chart_json(source: str) -> list[str]:
    """A chart written as JSON (`categories` + `series` with `values`/`data`/`points`)."""
    try:
        data = json.loads(source)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    series = [s for s in data.get("series") or [] if isinstance(s, dict)]
    if not series:
        return []
    columns: list[tuple[str, dict[str, str]]] = []
    order: list[str] = [str(c) for c in data.get("categories") or data.get("labels") or []]
    for item in series:
        values = item.get("values") or item.get("data") or item.get("points") or []
        cells: dict[str, str] = {}
        for i, value in enumerate(values):
            if isinstance(value, dict):
                key, value = str(value.get("x", value.get("label", i))), value.get("y")
            else:
                key = order[i] if i < len(order) else str(i + 1)
            if key not in order:
                order.append(key)
            cells[key] = "" if value is None else str(value)
        columns.append((str(item.get("name") or "값"), cells))
    if not order:
        return []
    rows = [[key, *(cells.get(key, "") for _, cells in columns)] for key in order]
    return _table(["항목", *(name for name, _ in columns)], rows, str(data.get("unit") or ""))


def _chart(lines: list[str], source: str) -> list[str]:
    """`kind | unit`, `axis | series…`, then rows, as a table; a JSON chart too."""
    if source.lstrip().startswith(("{", "[")):
        return _chart_json(source)
    if len(lines) < 3 or "|" not in lines[1]:
        return []
    head = [cell.strip() for cell in lines[0].split("|")]
    unit = head[1] if len(head) > 1 else ""
    names = [cell.strip() for cell in lines[1].split("|")]
    rows = [[cell.strip() for cell in line.split("|")] for line in lines[2:] if "|" in line]
    return _table([names[0] or "항목", *names[1:]], rows, unit) if rows else []


def _block(kind: str, body: list[str]) -> list[str]:
    lines = [line.strip() for line in body if line.strip()]
    if not lines:
        return []
    if kind == "kpi":
        return _kpi(lines)
    if kind == "steps":
        return _steps(lines)
    if kind == "cards":
        return _cards(lines)
    if kind == "callout":
        return _callout(lines)
    # a chart that does not read as numbers is dropped, never shown as its source
    return _chart(lines, "\n".join(body))


def plain(text: str) -> str:
    """`text` with each app block fence written as plain Markdown; other fences kept."""
    if not text or not re.search(r"(?:`{3,}|~{3,})[ \t]*(?:" + "|".join(KINDS) + ")", text, re.I):
        return text
    out: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        opened = _OPEN.match(lines[i])
        if opened is None:
            out.append(lines[i])
            i += 1
            continue
        mark = opened.group("mark")
        closer = re.compile(rf"^[ \t]{{0,3}}{re.escape(mark[0])}{{{len(mark)},}}[ \t]*$")
        j = i + 1
        while j < len(lines) and not closer.match(lines[j]):
            j += 1
        kind = _kind(opened.group("info"))
        if not kind:
            # any other fence (code, mermaid) as written, through its close
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        drawn = _block(kind, lines[i + 1:j])
        if drawn:
            if out and out[-1].strip():
                out.append("")
            out.extend(drawn)
            if j + 1 < len(lines) and lines[j + 1].strip():
                out.append("")
        i = j + 1
    return "\n".join(out)


__all__ = ["KINDS", "plain"]
