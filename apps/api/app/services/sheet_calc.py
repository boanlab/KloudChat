"""Calculates the formulas of an `.xlsx` in process, to keep each formula cell's cached value true.

A workbook the agent writes or edits has no cached values (or stale ones, once an input cell
changed), and everything that reads the file without a calculation engine (the shared page, the
agent's own `xlsx_read`, a previewer) shows what is cached. The editor's grid and Excel recalculate
on their own; this is for the rest.

Only what is exact is calculated: arithmetic, comparisons, `&`, cell and range references (also on
other sheets) and the common functions below. A formula that uses anything else keeps the value it
had, and nothing is guessed.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
import zipfile
from collections.abc import Callable
from typing import Any

import openpyxl
from lxml import etree
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils import column_index_from_string

from app.services import sheet_model, sheet_patch

_q = sheet_patch._q  # noqa: SLF001


class Err(str):
    """An Excel error value (`#DIV/0!`); it travels through a calculation like any value."""


class Unsupported(Exception):
    """The formula uses something this calculator does not do."""


class _Raise(Exception):
    """An error value cutting a calculation short, as Excel's errors do."""

    def __init__(self, err: Err) -> None:
        super().__init__(str(err))
        self.err = err


DIV0, VALUE = Err("#DIV/0!"), Err("#VALUE!")
NUM = Err("#NUM!")
MAX_CELLS = 200_000
MAX_DEPTH = 400

_ADDR = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_COL = re.compile(r"^\$?([A-Za-z]{1,3})$")
_ROW = re.compile(r"^\$?(\d+)$")


def _split_sheet(text: str, home: str) -> tuple[str, bool, str]:
    """(sheet, named, the reference without its sheet): `named` when the text had a sheet."""
    if "!" not in text:
        return home, False, text
    sheet, _, rest = text.rpartition("!")
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, True, rest


class _Book:
    def __init__(self, data: bytes) -> None:
        book = openpyxl.load_workbook(io.BytesIO(data))
        cached = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        self.folded = {ws.title.casefold(): ws.title for ws in book.worksheets}
        #: sheet -> {(row, col): constant}; 1-based
        self.values: dict[str, dict[tuple[int, int], Any]] = {}
        #: sheet -> {(row, col): formula text without "="}
        self.formulas: dict[str, dict[tuple[int, int], str]] = {}
        #: what a formula this cannot read (an array formula) holds
        self.stuck: dict[tuple[str, int, int], Any] = {}
        self.extent: dict[str, tuple[int, int]] = {}
        total = 0
        for ws in book.worksheets:
            vals: dict[tuple[int, int], Any] = {}
            forms: dict[tuple[int, int], str] = {}
            for row in ws.iter_rows():
                for cell in row:
                    raw = cell.value
                    if raw is None:
                        continue
                    total += 1
                    if total > MAX_CELLS:
                        raise Unsupported("too_big")
                    key = (cell.row, cell.column)
                    if cell.data_type == "f":
                        if isinstance(raw, str) and raw.startswith("="):
                            forms[key] = raw[1:]
                        else:
                            self.stuck[(ws.title, *key)] = cached[ws.title].cell(*key).value
                        continue
                    serial = sheet_model._serial(raw)  # noqa: SLF001
                    vals[key] = serial if serial is not None else raw
            self.values[ws.title] = vals
            self.formulas[ws.title] = forms
            self.extent[ws.title] = (max(ws.max_row, 1), max(ws.max_column, 1))
        self.memo: dict[tuple[str, int, int], Any] = {}
        self.busy: set[tuple[str, int, int]] = set()

    def cell(self, sheet: str, row: int, col: int, depth: int = 0) -> Any:
        key = (sheet, row, col)
        if key in self.memo:
            return self.memo[key]
        text = self.formulas[sheet].get((row, col))
        if text is None:
            if key in self.stuck:
                return self.stuck[key]
            return self.values[sheet].get((row, col))
        if key in self.busy or depth > MAX_DEPTH:
            raise Unsupported("circular")
        self.busy.add(key)
        try:
            value = _Parser(self, sheet, text, depth + 1).run()
        finally:
            self.busy.discard(key)
        self.memo[key] = value
        return value

    def resolve(self, sheet: str) -> str:
        found = self.folded.get(sheet.casefold())
        if found is None:
            raise Unsupported("no_sheet")
        return found

    def range(self, sheet: str, a: tuple[int, int], b: tuple[int, int], depth: int) -> list[list]:
        rows, cols = self.extent[sheet]
        r1, r2 = sorted((a[0], b[0]))
        c1, c2 = sorted((a[1], b[1]))
        r2, c2 = min(r2, rows), min(c2, cols)
        return [[self.cell(sheet, r, c, depth) for c in range(c1, c2 + 1)]
                for r in range(r1, r2 + 1)]


# -- values ----------------------------------------------------------------------------------

def _num(value: Any) -> float:
    """A value as a number the way Excel's arithmetic reads it."""
    if isinstance(value, Err):
        raise _Raise(value)
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            raise _Raise(VALUE) from None
    raise _Raise(VALUE)


def _text(value: Any) -> str:
    if isinstance(value, Err):
        raise _Raise(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, Err):
        raise _Raise(value)
    if isinstance(value, str):
        if value.upper() in ("TRUE", "FALSE"):
            return value.upper() == "TRUE"
        raise _Raise(VALUE)
    return bool(_num(value))


def _flat(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for arg in args:
        if isinstance(arg, list):
            for row in arg:
                out.extend(row)
        else:
            out.append(arg)
    return out


def _numbers(args: list[Any]) -> list[float]:
    """The numbers a SUM-like function takes: a range's text and blanks are skipped, a direct
    argument is converted."""
    out: list[float] = []
    for arg in args:
        if isinstance(arg, list):
            for value in (v for row in arg for v in row):
                if isinstance(value, Err):
                    raise _Raise(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.append(float(value))
        else:
            out.append(_num(arg))
    return out


def _round(value: float, digits: int, mode: str) -> float:
    scale = 10.0 ** digits
    scaled = abs(value) * scale
    if mode == "half":
        # Excel rounds half away from zero
        done = math.floor(scaled + 0.5 + 1e-9)
    elif mode == "up":
        done = math.ceil(scaled - 1e-9)
    else:
        done = math.floor(scaled + 1e-9)
    return math.copysign(done / scale, value) if value else 0.0


def _criterion(crit: Any) -> Callable[[Any], bool]:
    op: str = "="
    want: Any = crit
    if isinstance(crit, str):
        m = re.match(r"^(>=|<=|<>|>|<|=)?(.*)$", crit, re.S)
        op, rest = (m.group(1) or "="), m.group(2)
        try:
            want = float(rest)
        except ValueError:
            want = rest
    elif isinstance(crit, (int, float)) and not isinstance(crit, bool):
        want = float(crit)

    def test(value: Any) -> bool:
        if isinstance(want, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return op == "<>"
            a: Any = float(value)
            b: Any = want
        else:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return op == "<>"
            a, b = str("" if value is None else value).casefold(), str(want).casefold()
        return {"=": a == b, "<>": a != b, ">": a > b, "<": a < b, ">=": a >= b,
                "<=": a <= b}[op]
    return test


def _sumif(args: list[Any], count_only: bool) -> Any:
    if len(args) < 2 or not isinstance(args[0], list):
        raise Unsupported("args")
    test = _criterion(args[1])
    source = _flat([args[0]])
    target = _flat([args[2]]) if len(args) > 2 and isinstance(args[2], list) else source
    target = target + [None] * (len(source) - len(target))
    if count_only:
        return float(sum(1 for v in source if test(v)))
    return float(sum(t for v, t in zip(source, target, strict=False)
                     if test(v) and isinstance(t, (int, float)) and not isinstance(t, bool)))


def _sumproduct(args: list[Any]) -> Any:
    lists = [_flat([a]) for a in args]
    if not lists or len({len(x) for x in lists}) != 1:
        return VALUE
    total = 0.0
    for items in zip(*lists, strict=True):
        product = 1.0
        for item in items:
            if isinstance(item, Err):
                raise _Raise(item)
            product *= float(item) if isinstance(item, (int, float)) and not isinstance(
                item, bool) else 0.0
        total += product
    return total


def _power(a: float, b: float) -> Any:
    try:
        out = a ** b
    except ZeroDivisionError:
        return DIV0
    except OverflowError:
        return NUM
    return VALUE if isinstance(out, complex) else out


def _plain(args: list[Any], name: str) -> Any:
    """Functions of already calculated arguments."""
    if name == "SUM":
        return float(sum(_numbers(args)))
    if name in ("AVERAGE", "MIN", "MAX"):
        nums = _numbers(args)
        if not nums:
            return DIV0 if name == "AVERAGE" else 0.0
        if name == "AVERAGE":
            return sum(nums) / len(nums)
        return min(nums) if name == "MIN" else max(nums)
    if name == "COUNT":
        return float(sum(1 for v in _flat(args)
                         if isinstance(v, (int, float)) and not isinstance(v, bool)))
    if name == "COUNTA":
        return float(sum(1 for v in _flat(args) if v is not None and v != ""))
    if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
        if not 1 <= len(args) <= 2:
            raise Unsupported("args")
        digits = int(_num(args[1])) if len(args) == 2 else 0
        return _round(_num(args[0]), digits,
                      {"ROUND": "half", "ROUNDUP": "up", "ROUNDDOWN": "down"}[name])
    if name == "ABS":
        return abs(_num(args[0]))
    if name == "INT":
        return float(math.floor(_num(args[0])))
    if name == "MOD":
        a, b = _num(args[0]), _num(args[1])
        return DIV0 if b == 0 else a - b * math.floor(a / b)
    if name == "SQRT":
        value = _num(args[0])
        return NUM if value < 0 else math.sqrt(value)
    if name == "POWER":
        return _power(_num(args[0]), _num(args[1]))
    if name == "AND":
        return all(_bool(v) for v in _flat(args) if v is not None)
    if name == "OR":
        return any(_bool(v) for v in _flat(args) if v is not None)
    if name == "NOT":
        return not _bool(args[0])
    if name in ("CONCAT", "CONCATENATE"):
        return "".join(_text(v) for v in _flat(args))
    if name == "LEN":
        return float(len(_text(args[0])))
    if name == "SUMIF":
        return _sumif(args, False)
    if name == "COUNTIF":
        return _sumif(args, True)
    if name == "SUMPRODUCT":
        return _sumproduct(args)
    raise Unsupported(name)


def _compare(op: str, a: Any, b: Any) -> bool:
    for side in (a, b):
        if isinstance(side, Err):
            raise _Raise(side)
    if a is None:
        a = "" if isinstance(b, str) else 0.0
    if b is None:
        b = "" if isinstance(a, str) else 0.0
    if isinstance(a, str) != isinstance(b, str):
        # text sorts after numbers in Excel's order
        a, b = (1.0, 0.0) if isinstance(a, str) else (0.0, 1.0)
    elif isinstance(a, str):
        a, b = a.casefold(), b.casefold()
    else:
        a, b = float(a), float(b)
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


# -- the parser: tokens -> a tree, then a value ----------------------------------------------

_BINARY = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4,
           "/": 4, "^": 5}


class _Parser:
    def __init__(self, book: _Book, home: str, text: str, depth: int) -> None:
        self.tokens = [t for t in Tokenizer("=" + text).items if t.type != Token.WSPACE]
        self.pos = 0
        self.book, self.home, self.depth = book, home, depth

    def run(self) -> Any:
        try:
            node = self.expr(1)
            if self.pos != len(self.tokens):
                raise Unsupported("syntax")
            value = self.value(node)
            if isinstance(value, list):
                value = value[0][0] if value and value[0] else None
        except _Raise as raised:
            return raised.err
        except RecursionError:
            raise Unsupported("deep") from None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return NUM
        return value

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Token:
        if self.pos >= len(self.tokens):
            raise Unsupported("syntax")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expr(self, floor: int) -> tuple:
        left = self.unary()
        while True:
            token = self.peek()
            if token is None or token.type != Token.OP_IN:
                return left
            prec = _BINARY.get(token.value)
            if prec is None:
                raise Unsupported("operator")
            if prec < floor:
                return left
            self.take()
            left = ("bin", token.value, left, self.expr(prec + 1))

    def unary(self) -> tuple:
        token = self.peek()
        if token is not None and token.type == Token.OP_PRE:
            self.take()
            inner = self.unary()
            return ("neg", inner) if token.value == "-" else inner
        node = self.atom()
        while (nxt := self.peek()) is not None and nxt.type == Token.OP_POST:
            self.take()
            node = ("pct", node)
        return node

    def atom(self) -> tuple:
        token = self.take()
        if token.type == Token.OPERAND:
            if token.subtype == Token.NUMBER:
                return ("lit", float(token.value))
            if token.subtype == Token.TEXT:
                return ("lit", token.value[1:-1].replace('""', '"'))
            if token.subtype == Token.LOGICAL:
                return ("lit", token.value.upper() == "TRUE")
            if token.subtype == Token.ERROR:
                return ("lit", Err(token.value))
            return self.reference(token.value)
        if token.type == Token.PAREN and token.subtype == Token.OPEN:
            node = self.expr(1)
            close = self.take()
            if close.type != Token.PAREN or close.subtype != Token.CLOSE:
                raise Unsupported("syntax")
            return node
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            name = token.value[:-1].upper().removeprefix("_XLFN.")
            args: list[tuple] = []
            nxt = self.peek()
            if nxt is not None and nxt.type == Token.FUNC and nxt.subtype == Token.CLOSE:
                self.take()
                return ("call", name, args)
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.type == Token.SEP:
                    args.append(("lit", None))
                else:
                    args.append(self.expr(1))
                end = self.take()
                if end.type == Token.SEP:
                    continue
                if end.type == Token.FUNC and end.subtype == Token.CLOSE:
                    return ("call", name, args)
                raise Unsupported("syntax")
        raise Unsupported("syntax")

    def reference(self, text: str) -> tuple:
        sheet, named, rest = _split_sheet(text, self.home)
        if named:
            sheet = self.book.resolve(sheet)
        ends = rest.split(":")
        if len(ends) > 2:
            raise Unsupported("reference")
        rows, cols = self.book.extent[sheet]
        points: list[tuple[int, int]] = []
        for end in ends:
            if m := _ADDR.match(end):
                points.append((int(m.group(2)), column_index_from_string(m.group(1).upper())))
            elif len(ends) == 2 and (m := _COL.match(end)):
                points.append((1 if not points else rows,
                               column_index_from_string(m.group(1).upper())))
            elif len(ends) == 2 and (m := _ROW.match(end)):
                points.append((int(m.group(1)), 1 if not points else cols))
            else:
                # a defined name, a structured reference: not calculated here
                raise Unsupported("name")
        if len(points) == 1:
            return ("cell", sheet, points[0])
        return ("range", sheet, points[0], points[1])

    def value(self, node: tuple) -> Any:
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "cell":
            return self.book.cell(node[1], node[2][0], node[2][1], self.depth)
        if kind == "range":
            return self.book.range(node[1], node[2], node[3], self.depth)
        if kind == "neg":
            return -_num(self.scalar(node[1]))
        if kind == "pct":
            return _num(self.scalar(node[1])) / 100.0
        if kind == "bin":
            return self.binary(node[1], self.scalar(node[2]), self.scalar(node[3]))
        if kind == "call":
            return self.call(node[1], node[2])
        raise Unsupported(kind)

    def scalar(self, node: tuple) -> Any:
        value = self.value(node)
        if isinstance(value, list):
            # a range in arithmetic needs Excel's implicit intersection: not guessed
            if len(value) == 1 and len(value[0]) == 1:
                return value[0][0]
            raise Unsupported("array")
        return value

    def binary(self, op: str, a: Any, b: Any) -> Any:
        if op == "&":
            return _text(a) + _text(b)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return _compare(op, a, b)
        x, y = _num(a), _num(b)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            return DIV0 if y == 0 else x / y
        return _power(x, y)

    def call(self, name: str, nodes: list[tuple]) -> Any:
        if name == "IF":
            if not 2 <= len(nodes) <= 3:
                raise Unsupported("args")
            if _bool(self.scalar(nodes[0])):
                return self.value(nodes[1])
            return self.value(nodes[2]) if len(nodes) == 3 else False
        if name == "IFERROR":
            if len(nodes) != 2:
                raise Unsupported("args")
            try:
                value = self.value(nodes[0])
            except _Raise:
                return self.value(nodes[1])
            return self.value(nodes[1]) if isinstance(value, Err) else value
        return _plain([self.value(n) for n in nodes], name)


# -- the public side -------------------------------------------------------------------------

def values(data: bytes) -> dict[tuple[str, int, int], Any]:
    """Every formula cell this could calculate: (sheet, row, col), 1-based -> its value."""
    book = _Book(data)
    out: dict[tuple[str, int, int], Any] = {}
    for sheet, forms in book.formulas.items():
        for row, col in forms:
            try:
                out[(sheet, row, col)] = book.cell(sheet, row, col)
            except Unsupported:
                continue
    return out


def _put(cell: etree._Element, value: Any) -> None:
    for child in cell.findall(_q("v")):
        cell.remove(child)
    cell.attrib.pop("t", None)
    if isinstance(value, (dt.datetime, dt.date)):
        return
    if value is None:
        value = 0.0
    node = etree.SubElement(cell, _q("v"))
    if isinstance(value, Err):
        cell.set("t", "e")
        node.text = str(value)
    elif isinstance(value, bool):
        cell.set("t", "b")
        node.text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        number = float(value)
        node.text = str(int(number)) if number == int(number) and abs(number) < 1e15 \
            else repr(number)
    else:
        cell.set("t", "str")
        node.text = str(value)


def refresh(data: bytes) -> bytes:
    """`data` with every formula cell's cached value calculated (a cell that cannot be calculated
    keeps its own). Nothing else in the package changes."""
    try:
        calculated = values(data)
    except Exception:  # noqa: BLE001 - a workbook this cannot read keeps what it has
        return data
    if not calculated:
        return data
    src = zipfile.ZipFile(io.BytesIO(data))
    order = src.infolist()
    files = {i.filename: src.read(i.filename) for i in order}
    changed = False
    for name, path in sheet_patch._sheet_parts(files).items():  # noqa: SLF001
        root = etree.fromstring(files[path])
        touched = False
        for cell in root.iter(_q("c")):
            if cell.find(_q("f")) is None:
                continue
            m = _ADDR.match(cell.get("r", ""))
            if m is None:
                continue
            key = (name, int(m.group(2)), column_index_from_string(m.group(1).upper()))
            if key in calculated:
                _put(cell, calculated[key])
                touched = True
        if touched:
            files[path] = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                         standalone=True)
            changed = True
    if not changed:
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in order:
            if info.filename in files:
                zout.writestr(info.filename, files[info.filename])
    return out.getvalue()
