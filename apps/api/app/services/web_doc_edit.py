"""Read and edit a web report (Markdown sections) or a web deck (slides as JSON) in place, as
the office editors do their files (`hwpx_edit`, `docx_edit`): a numbered outline for the model,
and edit ops that change only what they name.

Ids are positions in the version read: a report's sections `s1…`, a deck's slides `s1…` (in
order, never a printed number). Ops that add, take out or move a part renumber what follows,
so one `apply` resolves every op against the version it was given and the next call reads
again. Every op or none: a refused op changes nothing.
"""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.services import office_edit, revise
from app.services.office_edit import BLANK, short

#: what a deck's design may be set to (the slides surface's own list)
VISUAL_STYLES = ("editorial", "minimal", "poster", "split", "dark", "slate", "warm", "pastel",
                 "forest", "paper", "mono")
FONTS = ("gothic", "serif")
_HEX = re.compile(r"#?([0-9A-Fa-f]{6})")

REPORT_OPS = (
    "replace_text", "set_section", "rename_section", "insert_section", "delete_section",
    "move_section", "fill_blank", "set_cell", "insert_table_row", "delete_table_row",
    "insert_table_column", "delete_table_column", "insert_table", "set_title", "set_head",
    "set_theme",
)
DECK_OPS = (
    "replace_text", "set_text", "set_list", "set_table", "fill_blank", "set_cell",
    "insert_table_row", "delete_table_row", "insert_table_column", "delete_table_column",
    "insert_slide", "delete_slide", "move_slide", "duplicate_slide", "set_title", "set_theme",
)
#: ops after which the part numbers are not the ones read
RENUMBERS = frozenset({"insert_section", "delete_section", "move_section", "insert_slide",
                       "delete_slide", "move_slide", "duplicate_slide"})
#: a slide's text fields and its list fields (`deck` layouts)
TEXT_FIELDS = ("title", "subtitle", "body", "notes")
LIST_FIELDS = ("bullets", "steps", "cards", "bands", "timeline", "tiles", "metrics")
#: how much of each part the outline shows
_PART_CHARS = 2500


class WebDocError(ValueError):
    """An op this module refuses; the message is shown to the model."""


@dataclass
class Result:
    data: dict
    title: str
    #: one Korean line per applied op
    changes: list[str]


# ── Markdown tables ──

_ROW = re.compile(r"^\s{0,3}\|.*\|\s*$")
_RULE = re.compile(r"^\s{0,3}\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


def _cells(line: str) -> list[str]:
    body = line.strip()[1:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def tables(text: str) -> list[tuple[int, int]]:
    """(first line, last line + 1) of each Markdown table in `text`, in order."""
    lines, out, at = text.split("\n"), [], 0
    while at < len(lines):
        if _ROW.match(lines[at]) and at + 1 < len(lines) and _RULE.match(lines[at + 1]):
            end = at + 2
            while end < len(lines) and _ROW.match(lines[end]) and not _RULE.match(lines[end]):
                end += 1
            out.append((at, end))
            at = end
        else:
            at += 1
    return out


def _grid(text: str, number: int) -> tuple[list[str], int, int, list[list[str]]]:
    found = tables(text)
    if not 1 <= number <= len(found):
        raise WebDocError(f"표 {number}이 없습니다 (이 절의 표 {len(found)}개).")
    lines = text.split("\n")
    start, end = found[number - 1]
    rows = [_cells(lines[start])] + [_cells(line) for line in lines[start + 2:end]]
    return lines, start, end, rows


def _put_grid(lines: list[str], start: int, end: int, rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    made = [_row(rows[0]), "| " + " | ".join("---" for _ in range(width)) + " |",
            *(_row(r) for r in rows[1:])]
    return "\n".join(lines[:start] + made + lines[end:])


# ── outline ──


def _part_name(part: dict, deck: bool) -> str:
    return str(part.get("title" if deck else "heading") or "").strip()


def outline(data: dict, title: str, *, deck: bool) -> str:
    """Numbered outline: the title and head, each part with its text (a report section's
    Markdown, its tables numbered; a slide's fields), then the blanks."""
    out = office_edit.Outline()
    parts = list(data.get("slides" if deck else "sections") or [])
    unit = "슬라이드" if deck else "절"
    out.line(f"제목: {title}")
    out.line(f"{unit} {len(parts)}개 (sN은 N번째 {unit}, 차례로 셉니다)")
    if not deck and (head := data.get("titleBlock")):
        fields = [f"{f[0]}={f[1] or '(비어 있음)'}" for f in head.get("fields") or []
                  if isinstance(f, (list, tuple)) and f]
        if fields:
            out.line("머리 칸: " + ", ".join(fields))
            for f in head.get("fields") or []:
                if isinstance(f, (list, tuple)) and len(f) > 1:
                    out.note(str(f[1] or ""), "머리 칸")
    design = data.get("design") or {}
    if design:
        out.line("디자인: " + ", ".join(f"{k}={v}" for k, v in design.items()
                                        if k in ("visualStyle", "accent", "font")))
    for index, part in enumerate(parts, 1):
        sid = f"s{index}"
        if deck:
            out.line(f"── {sid} ({part.get('layout') or 'bullets'}) ──")
            for name in ("title", "subtitle", "body"):
                if value := str(part.get(name) or "").strip():
                    out.line(f"  {name}: {value}")
                    out.note(value, sid)
            for name in LIST_FIELDS:
                items = part.get(name)
                if isinstance(items, list) and items:
                    shown = [" / ".join(map(str, i)) if isinstance(i, list) else str(i)
                             for i in items]
                    out.line(f"  {name}: " + " | ".join(shown))
                    for one in shown:
                        out.note(one, sid)
            if isinstance(part.get("rows"), list) and part["rows"]:
                out.line(f"  표 ({len(part['rows'])}줄 × {max(len(r) for r in part['rows'])}칸)")
                for r, row in enumerate(part["rows"]):
                    out.line(f"    r{r}: " + " | ".join(map(str, row)))
                    out.note(" ".join(map(str, row)), f"{sid} r{r}")
            if part.get("chart"):
                out.line("  (차트: 값은 set_table로 바꾸지 말고 그대로 두세요)")
            if notes := str(part.get("notes") or "").strip():
                out.line(f"  notes: {notes.replace(chr(10), ' / ')}")
                out.note(notes, f"{sid} notes")
            continue
        level = int(part.get("level") or 1)
        out.line(f"── {sid}{' (하위 절)' if level > 1 else ''}: {_part_name(part, False)} ──")
        body = str(part.get("content") or "")
        shown = body if len(body) <= _PART_CHARS else body[:_PART_CHARS] + " …(이하 생략)"
        count = len(tables(body))
        out.line(shown if shown.strip() else "(비어 있음)")
        if count:
            out.line(f"  (이 절의 표 {count}개: table 1~{count})")
        out.note(body, sid)
    return out.render()


# ── ops ──


def _ref(parts: list, at: object, *, zero: bool = False) -> int:
    text = str(at if at is not None else "").strip()
    if isinstance(at, int) or text.isdigit():
        text = f"s{text}"
    found = re.fullmatch(r"s(\d{1,4})", text)
    low = 0 if zero else 1
    if not found or not low <= int(found.group(1)) <= len(parts):
        raise WebDocError(f"{at!r}이 없습니다 ({'s0~' if zero else 's1~'}s{len(parts)}).")
    return int(found.group(1)) - 1


def _text(op: dict, name: str = "text", *, empty: bool = True) -> str:
    value = op.get(name)
    if isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
        value = "\n".join(map(str, value))
    if not isinstance(value, str):
        raise WebDocError(f"{op.get('op')}: {name}은 글자여야 합니다.")
    if not empty and not value.strip():
        raise WebDocError(f"{op.get('op')}: {name}이 비었습니다.")
    if len(value) > 20000:
        raise WebDocError(f"{op.get('op')}: {name}이 너무 깁니다.")
    return value.replace("\r\n", "\n")


def _int(op: dict, name: str) -> int:
    try:
        return int(op.get(name))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise WebDocError(f"{op.get('op')}: {name}은 0부터 세는 숫자입니다.") from exc


def _theme(data: dict, op: dict) -> str:
    design = dict(data.get("design") or {})
    said = []
    if op.get("visual_style") not in (None, ""):
        style = str(op["visual_style"]).strip().lower()
        if style not in VISUAL_STYLES:
            raise WebDocError(f"set_theme: visual_style은 {', '.join(VISUAL_STYLES)} 중 하나.")
        design["visualStyle"] = style
        said.append(style)
    if op.get("accent") not in (None, ""):
        found = _HEX.fullmatch(str(op["accent"]).strip())
        if not found:
            raise WebDocError("set_theme: accent는 '#1F3A68'처럼 16진 색입니다.")
        design["accent"] = "#" + found.group(1).upper()
        said.append(design["accent"])
    if op.get("font") not in (None, ""):
        if op["font"] not in FONTS:
            raise WebDocError("set_theme: font는 gothic 또는 serif입니다.")
        design["font"] = op["font"]
        said.append(op["font"])
    if not said:
        raise WebDocError("set_theme: visual_style, accent, font 중 하나는 주세요.")
    data["design"] = design
    return "디자인: " + ", ".join(said)


def _replace(text: str, find: str, new: str) -> tuple[str, int]:
    count = text.count(find)
    return text.replace(find, new), count


def _fill(text: str, blank: str, value: str) -> tuple[str, int]:
    return office_edit.filled(text, blank, value)


def _map_strings(value: Any, change) -> tuple[Any, int]:
    """Every string in a slide's fields changed by `change` (text -> (text, count))."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        total, out = 0, []
        for item in value:
            made, n = _map_strings(item, change)
            out.append(made)
            total += n
        return out, total
    if isinstance(value, dict):
        total, out = 0, {}
        for key, item in value.items():
            if key in ("id", "layout", "accent", "chart"):
                out[key] = item
                continue
            made, n = _map_strings(item, change)
            out[key] = made
            total += n
        return out, total
    return value, 0


def apply(data: dict, title: str, ops: list, *, deck: bool, request: str = "") -> Result:
    """`ops` on a copy of `data`: all or none. `request`: the person's words this turn, for
    the shortening check (`revise.shortening_loss`)."""
    office_edit.check_ops(ops, WebDocError)
    data = copy.deepcopy(data)
    key = "slides" if deck else "sections"
    parts: list[dict] = list(data.get(key) or [])
    allowed = DECK_OPS if deck else REPORT_OPS
    changes: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            raise WebDocError('op마다 {"op": ...} 객체여야 합니다.')
        kind = op.get("op")
        if kind not in allowed:
            raise WebDocError(f"모르는 op {kind!r}: {', '.join(allowed)} 중 하나.")
        if kind == "set_title":
            title = " ".join(_text(op, "title", empty=False).split())[:200]
            changes.append(f"제목 → 「{short(title)}」")
        elif kind == "set_theme":
            changes.append(_theme(data, op))
        elif kind == "set_head":
            head = dict(data.get("titleBlock") or {})
            fields = op.get("fields")
            names = [str(f[0]) for f in head.get("fields") or [] if f]
            if not isinstance(fields, dict) or not fields:
                raise WebDocError("set_head: fields는 {\"작성일\": \"…\"} 꼴입니다.")
            missing = [k for k in fields if k not in names]
            if missing:
                raise WebDocError(f"set_head: 머리 칸 {', '.join(missing)}이 없습니다 "
                                  f"(있는 칸: {', '.join(names) or '없음'}).")
            head["fields"] = [[f[0], str(fields.get(str(f[0]), f[1] if len(f) > 1 else ""))]
                              for f in head.get("fields") or [] if f]
            data["titleBlock"] = head
            changes.append("머리 칸: " + ", ".join(f"{k}={v}" for k, v in fields.items()))
        elif kind == "replace_text":
            find, new = _text(op, "find", empty=False), _text(op, "text")
            targets = [_ref(parts, op["section" if not deck else "slide"])] if op.get(
                "section" if not deck else "slide") not in (None, "") else range(len(parts))
            total = 0
            for i in targets:
                if deck:
                    parts[i], n = _map_strings(
                        parts[i], lambda t, f=find, v=new: _replace(t, f, v))
                else:
                    body, n = _replace(str(parts[i].get("content") or ""), find, new)
                    parts[i] = {**parts[i], "content": body}
                total += n
            if not total:
                raise WebDocError(f"replace_text: 「{short(find)}」이 없습니다. read의 글을 "
                                  "그대로 옮겨 find에 주세요.")
            changes.append(f"「{short(find)}」 → 「{short(new)}」 ({total}곳)")
        elif kind == "fill_blank":
            blank = str(op.get("blank") or "").strip()
            if not BLANK.fullmatch(blank):
                raise WebDocError(f"fill_blank: {blank!r}은 「[이름]」 꼴 빈칸이 아닙니다.")
            value = _text(op, "value")
            where = op.get("at")
            targets = [_ref(parts, where)] if where not in (None, "") else range(len(parts))
            total = 0
            for i in targets:
                if deck:
                    parts[i], n = _map_strings(
                        parts[i], lambda t, b=blank, v=value: _fill(t, b, v))
                else:
                    body, n = _fill(str(parts[i].get("content") or ""), blank, value)
                    parts[i] = {**parts[i], "content": body}
                total += n
            if not deck and where in (None, ""):
                head = data.get("titleBlock")
                if head:
                    for f in head.get("fields") or []:
                        if isinstance(f, list) and len(f) > 1 and blank in str(f[1]):
                            f[1], n = _fill(str(f[1]), blank, value)
                            total += n
            if not total:
                raise WebDocError(f"fill_blank: {blank}이 없습니다.")
            changes.append(f"{blank} → 「{short(value)}」 ({total}곳)")
        elif kind == "set_section":
            i = _ref(parts, op.get("section"))
            before = str(parts[i].get("content") or "")
            text = _text(op)
            if loss := revise.shortening_loss(before, text, request):
                raise WebDocError(f"set_section {op['section']}: {loss}")
            parts[i] = {**parts[i], "content": text, "format": "markdown", "status": "done"}
            changes.append(f"s{i + 1} 「{short(_part_name(parts[i], False))}」 고쳐 씀")
        elif kind == "rename_section":
            i = _ref(parts, op.get("section"))
            parts[i] = {**parts[i], "heading": " ".join(_text(op, "heading", empty=False).split())}
            changes.append(f"s{i + 1} 제목 → 「{short(parts[i]['heading'])}」")
        elif kind in ("insert_section", "insert_slide"):
            at = _ref(parts, op.get("after", f"s{len(parts)}"), zero=True)
            if deck:
                made = {"id": f"sl{uuid.uuid4().hex[:8]}",
                        "title": _text(op, "title", empty=False).strip(),
                        "layout": str(op.get("layout") or "bullets")}
                if isinstance(op.get("bullets"), list):
                    made["bullets"] = [str(b) for b in op["bullets"]][:8]
                if isinstance(op.get("rows"), list):
                    made["rows"] = office_edit.table_arg(op, WebDocError)
                    made["layout"] = "table"
                if op.get("notes"):
                    made["notes"] = _text(op, "notes")
                neighbour = parts[max(0, at)] if parts else {}
                if neighbour.get("accent"):
                    made["accent"] = neighbour["accent"]
                name = made["title"]
            else:
                made = {"id": uuid.uuid4().hex,
                        "heading": " ".join(_text(op, "heading", empty=False).split()),
                        "level": 2 if op.get("level") == 2 else 1, "status": "done",
                        "format": "markdown", "content": _text(op)}
                name = made["heading"]
            parts.insert(at + 1, made)
            changes.append(f"{'맨 앞' if at < 0 else f's{at + 1} 뒤'}에 「{short(name)}」 추가")
        elif kind in ("delete_section", "delete_slide"):
            i = _ref(parts, op.get("section" if not deck else "slide"))
            if len(parts) < 2:
                raise WebDocError("마지막 하나는 지울 수 없습니다.")
            gone = parts.pop(i)
            changes.append(f"s{i + 1} 「{short(_part_name(gone, deck))}」 삭제")
        elif kind in ("move_section", "move_slide"):
            i = _ref(parts, op.get("section" if not deck else "slide"))
            after = _ref(parts, op.get("after"), zero=True)
            if after in (i, i - 1):
                raise WebDocError("이미 그 자리에 있습니다.")
            name = _part_name(parts[i], deck)
            parts = revise.moved(parts, i, after)
            where = "맨 앞" if after < 0 else f"s{after + 1} 뒤"
            changes.append(f"「{short(name)}」을 {where}로 옮김")
        elif kind == "duplicate_slide":
            i = _ref(parts, op.get("slide"))
            made = {**copy.deepcopy(parts[i]), "id": f"sl{uuid.uuid4().hex[:8]}"}
            parts.insert(i + 1, made)
            changes.append(f"s{i + 1} 복제")
        elif kind == "set_text":
            i = _ref(parts, op.get("slide"))
            field = str(op.get("field") or "")
            if field not in TEXT_FIELDS:
                raise WebDocError(f"set_text: field는 {', '.join(TEXT_FIELDS)} 중 하나.")
            parts[i] = {**parts[i], field: _text(op)}
            changes.append(f"s{i + 1} {field} → 「{short(_text(op))}」")
        elif kind == "set_list":
            i = _ref(parts, op.get("slide"))
            field = str(op.get("field") or "bullets")
            items = op.get("items")
            if field not in LIST_FIELDS or not isinstance(items, list) or not items:
                raise WebDocError(f"set_list: field는 {', '.join(LIST_FIELDS)} 중 하나, items는 "
                                  "목록입니다.")
            if field == "bullets":
                items = [str(x) for x in items][:10]
            else:
                items = [[str(c) for c in x] if isinstance(x, list) else [str(x), ""]
                         for x in items][:8]
            parts[i] = {**parts[i], field: items}
            if field != "bullets" or parts[i].get("layout") in ("table", "chart"):
                parts[i]["layout"] = field if field != "bullets" else "bullets"
            changes.append(f"s{i + 1} {field} {len(items)}개")
        elif kind == "set_table":
            i = _ref(parts, op.get("slide"))
            rows = office_edit.table_arg(op, WebDocError)
            slide = {k: v for k, v in parts[i].items() if k not in LIST_FIELDS and k != "chart"}
            parts[i] = {**slide, "layout": "table", "rows": rows}
            changes.append(f"s{i + 1} 표로 ({len(rows)}줄 × {len(rows[0])}칸)")
        elif kind in ("set_cell", "insert_table_row", "delete_table_row", "insert_table_column",
                      "delete_table_column", "insert_table"):
            changes.append(_table_op(parts, op, deck))
        else:
            raise WebDocError(f"모르는 op {kind!r}")
    data[key] = parts
    return Result(data, title, changes)


def _table_op(parts: list[dict], op: dict, deck: bool) -> str:
    kind = op["op"]
    where = "slide" if deck else "section"
    i = _ref(parts, op.get(where))
    if kind == "insert_table":
        if deck:
            raise WebDocError("슬라이드는 set_table을 쓰세요.")
        rows = office_edit.table_arg(op, WebDocError)
        body = str(parts[i].get("content") or "").rstrip("\n")
        made = _put_grid([], 0, 0, rows)
        parts[i] = {**parts[i], "content": f"{body}\n\n{made}" if body else made}
        return f"s{i + 1} 끝에 표 추가 ({len(rows)}줄 × {len(rows[0])}칸)"
    if deck:
        rows = [list(map(str, r)) for r in parts[i].get("rows") or []]
        if not rows:
            raise WebDocError(f"s{i + 1}에는 표가 없습니다.")
        lines, start, end = [], 0, 0
    else:
        number = int(op.get("table") or 1)
        lines, start, end, rows = _grid(str(parts[i].get("content") or ""), number)
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    if kind == "set_cell":
        r, c = _int(op, "row"), _int(op, "col")
        if not (0 <= r < len(rows) and 0 <= c < width):
            raise WebDocError(f"r{r}c{c} 칸이 없습니다 (표 {len(rows)}×{width}, 0부터).")
        rows[r][c] = " ".join(_text(op).split())
        said = f"s{i + 1} 표 r{r}c{c} → 「{short(rows[r][c])}」"
    elif kind == "insert_table_row":
        r = _int(op, "after")
        if not 0 <= r < len(rows):
            raise WebDocError(f"r{r} 줄이 없습니다.")
        rows.insert(r + 1, [""] * width)
        said = f"s{i + 1} 표 r{r} 아래에 줄 추가"
    elif kind == "delete_table_row":
        r = _int(op, "row")
        if not 1 <= r < len(rows):
            raise WebDocError(f"r{r} 줄을 지울 수 없습니다 (머리 줄 r0 제외, 1~{len(rows) - 1}).")
        rows.pop(r)
        said = f"s{i + 1} 표 r{r} 줄 삭제"
    elif kind == "insert_table_column":
        c = _int(op, "after")
        if not 0 <= c < width:
            raise WebDocError(f"c{c} 열이 없습니다.")
        header = str(op.get("text") or "")
        for n, row in enumerate(rows):
            row.insert(c + 1, header if n == 0 else "")
        said = f"s{i + 1} 표 c{c} 오른쪽에 열 추가"
    else:
        c = _int(op, "col")
        if not 0 <= c < width or width < 2:
            raise WebDocError(f"c{c} 열을 지울 수 없습니다.")
        for row in rows:
            row.pop(c)
        said = f"s{i + 1} 표 c{c} 열 삭제"
    if deck:
        parts[i] = {**parts[i], "rows": rows}
    else:
        parts[i] = {**parts[i], "content": _put_grid(lines, start, end, rows)}
    return said


def blanks(data: dict, *, deck: bool) -> list[str]:
    seen: dict[str, None] = {}
    for part in data.get("slides" if deck else "sections") or []:
        text = " ".join(_strings(part)) if deck else str(part.get("content") or "")
        for found in BLANK.finditer(text):
            seen.setdefault(found.group(0))
    return list(seen)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() if k not in ("id", "layout", "accent")
                for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []
