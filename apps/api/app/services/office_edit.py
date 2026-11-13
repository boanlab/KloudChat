"""What the HWP and Word editors share: the bracket blanks, the particle that agrees with a
filled value, op arguments, and the numbered outline the model reads (`hwpx_edit`,
`docx_edit`)."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A blank left for the person: 「[기관명]」, 「[YYYY. MM. DD.]」; not 「[1]」 citations.
BLANK = re.compile(r"\[(?!\d{1,3}\])[^\[\]\n]{1,40}\]")

#: Outline cap, so a long file does not drown a small model.
MAX_OUTLINE = 12000
MAX_OPS = 60
MAX_TEXT = 4000

OPS = (
    "replace_text", "fill_blank", "set_cell", "insert_paragraph", "delete_paragraph",
    "insert_table_row", "delete_table_row", "insert_table",
)
#: a new table's size: rows (its head row included) and columns
MAX_TABLE_ROWS, MAX_TABLE_COLS = 40, 8


@dataclass
class Result:
    data: bytes
    #: one Korean line per applied op
    changes: list[str]


#: particle pairs: after a final consonant, after a vowel
_PARTICLES = {"을": ("을", "를"), "를": ("을", "를"), "이": ("이", "가"), "가": ("이", "가"),
              "은": ("은", "는"), "는": ("은", "는"), "과": ("과", "와"), "와": ("과", "와")}


def _closed(word: str) -> bool | None:
    """The last syllable has a final consonant; None when it is not Hangul."""
    if not word or not "가" <= word[-1] <= "힣":
        return None
    return (ord(word[-1]) - 0xAC00) % 28 != 0


def blank_with_particle(old: str) -> re.Pattern[str]:
    """`old`, and the particle right after it as group 1."""
    return re.compile(re.escape(old) + r"(으로|로|을|를|이|가|은|는|과|와)?")


def put(new: str, particle: str | None) -> str:
    """`new` and a particle agreeing with it (「[연수명]을」 → 「교원연수를」)."""
    particle = particle or ""
    closed = _closed(new)
    if not particle or closed is None:
        return new + particle
    if particle in ("으로", "로"):
        final = (ord(new[-1]) - 0xAC00) % 28
        return new + ("으로" if closed and final != 8 else "로")
    return new + _PARTICLES[particle][0 if closed else 1]


def filled(text: str, old: str, new: str) -> tuple[str, int]:
    """`old` replaced by `new` in `text`, each particle after it agreeing with `new`."""
    return blank_with_particle(old).subn(lambda found: put(new, found.group(1)), text)


def short(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def text_arg(op: dict, name: str, error: type[ValueError]) -> str:
    value = op.get(name)
    if not isinstance(value, str):
        raise error(f"{op.get('op')}: {name}은 글자여야 합니다.")
    if len(value) > MAX_TEXT:
        raise error(f"{op.get('op')}: {name}이 {MAX_TEXT}자를 넘습니다.")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def table_arg(op: dict, error: type[ValueError]) -> list[list[str]]:
    """`rows` of an `insert_table`: lists of cell text, the first row its head; short rows
    padded to the widest."""
    rows = op.get("rows")
    if (not isinstance(rows, list) or not rows
            or not all(isinstance(row, list) and row for row in rows)):
        raise error("insert_table: rows는 [[머리 칸, …], [칸, …], …] 꼴이어야 합니다.")
    width = max(len(row) for row in rows)
    if len(rows) > MAX_TABLE_ROWS or width > MAX_TABLE_COLS:
        raise error(f"insert_table: 표는 {MAX_TABLE_ROWS}줄 × {MAX_TABLE_COLS}칸까지입니다.")
    out: list[list[str]] = []
    for row in rows:
        cells = []
        for cell in row:
            if not isinstance(cell, (str, int, float)):
                raise error("insert_table: 칸은 글자여야 합니다.")
            text = " ".join(str(cell).split())
            if len(text) > MAX_TEXT:
                raise error(f"insert_table: 한 칸이 {MAX_TEXT}자를 넘습니다.")
            # a 「|」 would split the cell when the exporters read the row
            cells.append(text.replace("|", "/"))
        out.append(cells + [""] * (width - len(cells)))
    return out


def check_ops(ops: object, error: type[ValueError]) -> list:
    if not isinstance(ops, list) or not ops:
        raise error("ops가 비었습니다.")
    if len(ops) > MAX_OPS:
        raise error(f"한 번에 {MAX_OPS}개까지 고칠 수 있습니다.")
    return ops


def unknown_op(kind: object, error: type[ValueError]) -> ValueError:
    return error(f"모르는 op {kind!r}: {', '.join(OPS)} 중 하나.")


class Outline:
    """The numbered outline: `p3: text`, a table's rows `r0: a | b`, a run of empty
    paragraphs said once, then every blank with the places it stands."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.places: dict[str, list[str]] = {}
        #: a run of empty lines, said once: 「p4~p14: (빈 줄 11개)」
        self._empty: list[int] = []

    def _said(self) -> None:
        empty = self._empty
        if len(empty) == 1:
            self.lines.append(f"p{empty[0]}: (빈 줄)")
        elif empty:
            self.lines.append(f"p{empty[0]}~p{empty[-1]}: (빈 줄 {len(empty)}개)")
        empty.clear()

    def empty(self, index: int) -> None:
        self._empty.append(index)

    def paragraph(self, index: int, text: str, mark: str = "") -> None:
        self._said()
        pid = f"p{index}"
        if text.strip():
            self.lines.append(f"{pid}{mark}: {text}")
        self.note(text, pid)

    def note(self, text: str, where: str) -> None:
        for found in BLANK.finditer(text):
            self.places.setdefault(found.group(0), []).append(where)

    def table(
        self, tid: str, shape: tuple[int, int], merged: bool,
        cells: list[tuple[int, int, str]], where: str = "",
    ) -> None:
        self._said()
        note = ", 병합 셀 있음" if merged else ""
        inside = f", {where} 안" if where else ""
        self.lines.append(f"{tid} (표 {shape[0]}×{shape[1]}{note}{inside})")
        by_row: dict[int, list[tuple[int, str]]] = {}
        for r, c, text in cells:
            text = text.replace("\n", " / ")
            by_row.setdefault(r, []).append((c, text))
            self.note(text, f"{tid} r{r}c{c}")
        for r in sorted(by_row):
            self.lines.append(f"  r{r}: " + " | ".join(t for _, t in sorted(by_row[r])))

    def line(self, text: str) -> None:
        self._said()
        self.lines.append(text)

    def render(self) -> str:
        self._said()
        lines = list(self.lines)
        if self.places:
            lines.append(
                "빈칸: " + ", ".join(f"{b} ({', '.join(at)})" for b, at in self.places.items())
            )
        else:
            lines.append("빈칸: 없음")
        text = "\n".join(lines)
        if len(text) > MAX_OUTLINE:
            text = text[:MAX_OUTLINE] + "\n…(이하 생략)"
        return text
