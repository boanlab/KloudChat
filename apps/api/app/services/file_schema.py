"""What a data file looks like, computed by code and handed to the code model.

Sheets and tables are shown as JSON arrays, one row per line: a schema written
with " | " between cells gets parsed by the model as if the file held pipes.
Every reader is stdlib (plus pypdf), the same footing as `services.files`.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.files import _member, _open_zip

PREVIEW_ROWS = 5
SCAN_ROWS = 20
MAX_SCHEMA_CHARS = 3_000

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
#: Built-in number formats that are dates.
_DATE_FORMAT_IDS = set(range(14, 23)) | {45, 46, 47}
_EPOCH = dt.datetime(1899, 12, 30)
_COLUMN = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


# ── xlsx ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Sheet:
    name: str
    rows: list[list] = field(default_factory=list)
    formulas: int = 0
    #: Formula cells with no cached value: the file was written by code, never opened.
    uncached: int = 0
    #: Stopped at `max_rows`.
    truncated: bool = False


def _column_index(ref: str) -> int:
    match = _COLUMN.match(ref)
    if not match:
        return -1
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _date_styles(archive) -> set[int]:
    """Indexes into cellXfs whose number format shows a date."""
    try:
        root = ET.fromstring(_member(archive, "xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = {
        int(fmt.get("numFmtId", "0")): fmt.get("formatCode", "")
        for fmt in root.iterfind("m:numFmts/m:numFmt", _NS)
    }
    dated = set()
    for index, xf in enumerate(root.iterfind("m:cellXfs/m:xf", _NS)):
        fmt_id = int(xf.get("numFmtId", "0"))
        code = re.sub(r'"[^"]{0,40}"|\[[^\]]{0,40}\]', "", custom.get(fmt_id, "")).lower()
        if fmt_id in _DATE_FORMAT_IDS or (code and re.search(r"[yd]", code)):
            dated.add(index)
    return dated


def _sheet_paths(archive) -> list[tuple[str, str]]:
    """`(sheet name, member path)` in workbook order."""
    try:
        book = ET.fromstring(_member(archive, "xl/workbook.xml"))
        rels = ET.fromstring(_member(archive, "xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        names = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet"))
        return [(PurePosixPath(n).stem, n) for n in names]
    targets = {
        rel.get("Id"): rel.get("Target", "") for rel in rels.iterfind("rel:Relationship", _NS)
    }
    out = []
    for sheet in book.iterfind("m:sheets/m:sheet", _NS):
        target = targets.get(sheet.get(_R_ID), "")
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        out.append((sheet.get("name", ""), path))
    return out


def read_xlsx(data: bytes, *, max_rows: int | None = None) -> list[Sheet]:
    """Every sheet's cell values: str, int/float, datetime or None. Cached values for formulas."""
    with _open_zip(data) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(_member(archive, "xl/sharedStrings.xml"))
            for item in root.iterfind("m:si", _NS):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t")))
        dated = _date_styles(archive)
        sheets = []
        for name, path in _sheet_paths(archive):
            sheet = Sheet(name)
            try:
                root = ET.fromstring(_member(archive, path))
            except (KeyError, ET.ParseError):
                sheets.append(sheet)
                continue
            for row in root.iterfind("m:sheetData/m:row", _NS):
                if max_rows is not None and len(sheet.rows) >= max_rows:
                    sheet.truncated = True
                    break
                at = int(row.get("r", len(sheet.rows) + 1)) - 1
                while len(sheet.rows) < at:
                    sheet.rows.append([])
                cells: list = []
                for cell in row.iterfind("m:c", _NS):
                    col = _column_index(cell.get("r", ""))
                    col = len(cells) if col < 0 else col
                    while len(cells) < col:
                        cells.append(None)
                    kind = cell.get("t", "n")
                    value_node = cell.find("m:v", _NS)
                    raw = value_node.text if value_node is not None else None
                    if cell.find("m:f", _NS) is not None:
                        sheet.formulas += 1
                        if raw is None:
                            sheet.uncached += 1
                    value: object = None
                    if kind == "s" and raw is not None:
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = None
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{{{_NS['m']}}}t"))
                    elif kind in ("str", "e"):
                        value = raw
                    elif kind == "b":
                        value = raw == "1"
                    elif raw is not None:
                        try:
                            number = float(raw)
                        except ValueError:
                            number = None
                        if number is None:
                            value = raw
                        elif int(cell.get("s", "0")) in dated and 0 < number < 2_958_466:
                            value = _EPOCH + dt.timedelta(days=number)
                        else:
                            value = int(number) if number.is_integer() else number
                    cells.append(value)
                sheet.rows.append(cells)
            while sheet.rows and not any(_filled(v) for v in sheet.rows[-1]):
                sheet.rows.pop()
            sheets.append(sheet)
    return sheets


def _filled(value) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _plain(value):
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d") if value.time() == dt.time() else value.isoformat(" ")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _texty(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if len(value) > 60:
        return True
    return not _NUMBER.match(value.strip().replace(" ", ""))


def header_row(rows: list[list]) -> int:
    """Index of the likeliest header: the first wide, mostly-text row with data below."""
    counts = [sum(_filled(v) for v in row) for row in rows[:SCAN_ROWS]]
    widest = max(counts or [0])
    if not widest:
        return 0
    for index, row in enumerate(rows[:SCAN_ROWS]):
        texty = sum(_texty(v) for v in row)
        below = any(sum(_filled(v) for v in r) >= 2 for r in rows[index + 1 : index + 4])
        if (
            texty >= min(2, widest)
            and counts[index] >= 0.6 * widest
            and texty >= 0.6 * counts[index]
            and below
        ):
            return index
    return next((i for i, n in enumerate(counts) if n == widest), 0)


def _json_row(row: list, width: int) -> str:
    cells = [_plain(v) for v in (list(row) + [None] * width)[:width]]
    return json.dumps(cells, ensure_ascii=False, default=str)


def _table_lines(rows: list[list], *, more: bool = False) -> list[str]:
    width = max((len(r) for r in rows), default=0)
    while width and not any(len(r) >= width and _filled(r[width - 1]) for r in rows):
        width -= 1
    if not width:
        return ["  (빈 표)"]
    at = header_row(rows)
    header = [str(_plain(v)) if _filled(v) else f"(빈 열 {i + 1})" for i, v in enumerate(
        (list(rows[at]) + [None] * width)[:width]
    )]
    body = rows[at + 1 :]
    data = [r for r in body if any(_filled(v) for v in r)]
    lines = [
        f"  머리글 추정: {at + 1}행",
        f"  열({width}): {json.dumps(header, ensure_ascii=False)}",
        f"  머리글 아래 비지 않은 행: {len(data)}{' 이상' if more else ''}",
    ]
    above = [r for r in rows[:at] if any(_filled(v) for v in r)]
    if above:
        lines.append("  머리글 위 행(제목·단위 표기 등):")
        for r in above[:3]:
            filled = [_plain(v) for v in r if _filled(v)]
            lines.append(f"    {json.dumps(filled, ensure_ascii=False, default=str)}")
    lines.append(f"  처음 {min(PREVIEW_ROWS, len(data))}행(행마다 JSON 배열):")
    lines += [f"    {_json_row(r, width)}" for r in data[:PREVIEW_ROWS]]
    if len(data) > PREVIEW_ROWS:
        lines.append("  마지막 2행(합계·주석 행인지 확인):")
        lines += [f"    {_json_row(r, width)}" for r in data[-2:]]
    return lines


def _xlsx(data: bytes) -> list[str]:
    sheets = read_xlsx(data, max_rows=5_000)
    lines = [f"시트 {len(sheets)}개"]
    for sheet in sheets:
        more = sheet.truncated
        rows = sheet.rows
        lines.append(f"- 시트 {json.dumps(sheet.name, ensure_ascii=False)}: {len(rows)}행"
                     + (" 이상" if more else ""))
        if sheet.formulas:
            lines.append(
                f"  수식 {sheet.formulas}칸"
                + (f", 그중 저장된 계산값이 없는 칸 {sheet.uncached}" if sheet.uncached else "")
            )
        if rows:
            lines += _table_lines(rows, more=more)
    return lines


# ── csv ─────────────────────────────────────────────────────────────────


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            text = data.decode(encoding)
            return text, "utf-8" if encoding == "utf-8-sig" and not data.startswith(
                b"\xef\xbb\xbf"
            ) else encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _csv(data: bytes) -> list[str]:
    text, encoding = decode_text(data)
    try:
        delimiter = csv.Sniffer().sniff(text[:20_000], delimiters=",\t;|").delimiter
    except csv.Error:
        delimiter = ","
    rows = [list(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = [[c if c != "" else None for c in r] for r in rows]
    shown = {",": "쉼표", "\t": "탭", ";": "세미콜론", "|": "세로 막대"}[delimiter]
    lines = [
        f"인코딩 {encoding} (pandas: encoding={encoding!r}), 구분자 {shown} "
        f"(sep={delimiter!r}), 전체 {len(rows)}줄"
    ]
    return lines + _table_lines(rows)


# ── documents ───────────────────────────────────────────────────────────


def _quoted(lines: list[str], limit: int = 8) -> list[str]:
    kept = [ln.strip() for ln in lines if ln.strip()][:limit]
    return [f"    {json.dumps(ln[:160], ensure_ascii=False)}" for ln in kept]


def _pdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    count = len(reader.pages)
    lines = [f"PDF {count}쪽 (줄마다 따옴표로 감싼 글자 그대로; 따옴표는 파일 내용이 아님)"]
    empty = 0
    for number, page in enumerate(reader.pages[:2], start=1):
        try:
            body = page.extract_text(extraction_mode="layout") or ""
        except Exception:  # noqa: BLE001 — one bad page must not lose the schema
            body = ""
        if not body.strip():
            empty += 1
        lines.append(f"- {number}쪽 처음 줄:")
        lines += _quoted(body.splitlines()) or ["    (글자 없음)"]
    if empty and empty == min(2, count):
        lines.append("  글자가 없습니다: 스캔본이면 글자 인식(OCR)이 필요합니다.")
    return lines


def _docx(data: bytes) -> list[str]:
    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with _open_zip(data) as archive:
        root = ET.fromstring(_member(archive, "word/document.xml"))
    paragraphs = [
        "".join(t.text or "" for t in p.iter(f"{word}t")) for p in root.iter(f"{word}p")
    ]
    tables = sum(1 for _ in root.iter(f"{word}tbl"))
    filled = [p for p in paragraphs if p.strip()]
    return [f"문단 {len(filled)}개, 표 {tables}개", "- 처음 문단:", *_quoted(filled)]


def _pptx(data: bytes) -> list[str]:
    drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    with _open_zip(data) as archive:
        slides = sorted(
            (n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d{1,4}\.xml", n)),
            key=lambda n: int(re.sub(r"\D", "", PurePosixPath(n).stem) or 0),
        )
        lines = [f"슬라이드 {len(slides)}장"]
        for number, name in enumerate(slides[:3], start=1):
            root = ET.fromstring(_member(archive, name))
            texts = [
                "".join(t.text or "" for t in p.iter(f"{drawing}t"))
                for p in root.iter(f"{drawing}p")
            ]
            lines.append(f"- {number}장:")
            lines += _quoted(texts, 5) or ["    (글자 없음)"]
    return lines


def _text(data: bytes) -> list[str]:
    text, encoding = decode_text(data)
    lines = text.splitlines()
    return [f"텍스트 {len(lines)}줄, 인코딩 {encoding}", "- 처음 줄:", *_quoted(lines)]


_READERS = {
    ".xlsx": _xlsx, ".xlsm": _xlsx, ".csv": _csv, ".tsv": _csv, ".pdf": _pdf,
    ".docx": _docx, ".pptx": _pptx, ".txt": _text, ".md": _text, ".json": _text,
}


def _size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def describe(name: str, data: bytes) -> str:
    """One file's schema block; never raises."""
    suffix = PurePosixPath(name).suffix.lower()
    kind = suffix.lstrip(".").upper() or "?"
    head = f"파일 {json.dumps(name, ensure_ascii=False)} ({kind}, {_size(len(data))})"
    reader = _READERS.get(suffix)
    if reader is None:
        return f"{head}\n  내용 미리보기 없음"
    try:
        body = "\n".join(reader(data))
    except Exception as exc:  # noqa: BLE001 — a schema is a hint, not a gate
        body = f"  읽지 못함 ({type(exc).__name__})"
    text = f"{head}\n{body}"
    return text if len(text) <= MAX_SCHEMA_CHARS else text[:MAX_SCHEMA_CHARS] + "\n  …(생략)"


# ── output checks ───────────────────────────────────────────────────────


def check_output(name: str, data: bytes) -> str:
    """A short code-made check of a produced file: does it open, how much is in it."""
    suffix = PurePosixPath(name).suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm"):
            sheets = read_xlsx(data)
            parts = [f"{s.name} {len(s.rows) - 1 if s.rows else 0}행" for s in sheets]
            note = f"엑셀 열림 · 시트 {len(sheets)}개({', '.join(parts)})"
            uncached = sum(s.uncached for s in sheets)
            if uncached:
                note += f" · 계산값 없는 수식 {uncached}칸(엑셀에서 열면 계산됨)"
            return note
        if suffix == ".pdf":
            from pypdf import PdfReader

            return f"PDF 열림 · {len(PdfReader(io.BytesIO(data)).pages)}쪽"
        if suffix in (".csv", ".tsv"):
            text, encoding = decode_text(data)
            rows = sum(1 for _ in csv.reader(io.StringIO(text)))
            return f"CSV {rows}줄 · {encoding}"
        if suffix == ".docx":
            return "워드 열림 · " + _docx(data)[0]
        if suffix == ".pptx":
            return "파워포인트 열림 · " + _pptx(data)[0]
        if suffix == ".zip":
            with _open_zip(data) as archive:
                return f"압축 파일 · {len(archive.namelist())}개 항목"
        if suffix in (".png", ".jpg", ".jpeg"):
            return "그림"
    except Exception as exc:  # noqa: BLE001 — the check reports, it never fails the run
        return f"열리지 않음 ({type(exc).__name__})"
    return ""
