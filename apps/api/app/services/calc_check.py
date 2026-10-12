"""Slide numbers corrected against code-computed values; only unambiguous claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: slide number; letters in 「P1」「q=2」 excluded
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.,])-?(?:\d{1,3}(?:,\d{3})+|\d{1,15})(?:\.\d{1,10})?(?![\d])")
#: counts of other things: 「2배」「30%」「3장」
_OTHER_QUANTITY = re.compile(r"\s{0,2}(?:배|%|퍼센트|장|개|번|명|차|회|위|등|단계|가지|줄|쪽)")
_PAREN = re.compile(r"[(（][^()（）]{0,80}[)）]")
#: allowed between a name's letters: spaces, brackets, 「=」
_GAP = r"[^0-9A-Za-z가-힣]{0,3}"


@dataclass(frozen=True, slots=True)
class Stated:
    """One printed value: 「FCFS | 평균 대기시간 = 4.75」 → row, label, 4.75."""

    row: str
    label: str
    text: str
    value: float


def stated_values(block: str) -> list[Stated]:
    """Single-number values of a computed block, by row and label."""
    out: list[Stated] = []
    for line in (block or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            row, named = cells[0], [c for c in cells[1:] if "=" in c]
        else:
            row, named = "", [line]
        for cell in named:
            label, value = cell.split("=", 1)
            numbers = _NUMBER.findall(value)
            if len(numbers) == 1 and label.strip():
                out.append(Stated(row, label.strip(), numbers[0],
                                  float(numbers[0].replace(",", ""))))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def _pattern(name: str) -> re.Pattern[str] | None:
    """name with optional inner spacing/brackets, not inside a longer word."""
    if len(name) < 2:
        return None
    body = _GAP.join(re.escape(ch) for ch in name)
    head = r"(?<![A-Za-z])" if re.match(r"[a-z]", name) else ""
    tail = r"(?![A-Za-z0-9])" if re.search(r"[a-z0-9]$", name) else ""
    return re.compile(head + body + tail, re.I)


#: English measure words and the Korean a text uses for them
_KO_MEASURE = (("wait", "대기"), ("turnaround", "반환"), ("response", "응답"),
               ("completion", "완료"), ("burst", "실행"))
_AVERAGE = re.compile(r"(?<![A-Za-z])(?:average|avg|mean)(?![A-Za-z])", re.I)


def _spelled_out(acronym: str) -> str:
    """「SRTF」 written as its words: 「Shortest Remaining Time First」, 「First-Come, …」."""
    return r"(?<![A-Za-z])" + r"[^A-Za-z0-9가-힣\n]{1,3}".join(
        f"{ch.upper()}[a-z]+" for ch in acronym) + r"(?![A-Za-z])"


def _initials(words: str) -> str:
    """「Round Robin」 → 「RR」; '' unless two or more capitalised English words."""
    parts = re.findall(r"[A-Za-z]+", words)
    if len(parts) < 2 or not all(p[0].isupper() for p in parts):
        return ""
    return "".join(p[0] for p in parts)


class _Names:
    """Slide spellings of each row and label."""

    def __init__(self, stated: list[Stated]) -> None:
        rows = list(dict.fromkeys(s.row for s in stated if s.row))
        bare = [_PAREN.sub("", r).strip() for r in rows]
        cores = [_norm(b) for b in bare]
        self.rows: dict[str, list[re.Pattern[str]]] = {}
        for row, plain, core in zip(rows, bare, cores, strict=True):
            names = {_norm(row)}
            # 「SJF(비선점)」 → 「SJF」 unless ambiguous (RR(q=2)/RR(q=4))
            if core and cores.count(core) == 1:
                names.add(core)
            patterns = [p for n in sorted(names, key=len, reverse=True) if (p := _pattern(n))]
            # the other spelling of the same name: 「SRTF」 ↔ 「Shortest Remaining Time First」
            other = ""
            if re.fullmatch(r"[A-Z]{2,5}", plain):
                other = _spelled_out(plain)
            elif short := _initials(plain):
                other = r"(?<![A-Za-z])" + short + r"(?![A-Za-z])"
            spellings = [s for s in (other, (_pattern(core).pattern if core and _pattern(core)
                                              else "")) if s]
            numbers = re.findall(r"\d+", row[len(plain):] if row.startswith(plain) else "")
            if core and cores.count(core) == 1 and other:
                patterns.append(re.compile(other))
            elif core and cores.count(core) > 1 and len(numbers) == 1:
                # 「RR(q=2)」 as 「Round Robin (타임 퀀텀 = 2)」: the name, then its own number
                for spelling in spellings:
                    patterns.append(re.compile(
                        f"(?:{spelling})" + r"[^0-9\n|]{0,20}?(?<![0-9.])" + numbers[0]
                        + r"(?![0-9])", re.I if spelling != other else 0))
            self.rows[row] = patterns
        self.labels: dict[tuple[str, str], list[re.Pattern[str]]] = {}
        for s in stated:
            full = _norm(s.label)
            names = {full}
            # prefix alias (「평균 대기」) when unique in the row
            prefix = full[:4]
            if len(full) > 4 and re.fullmatch(r"[가-힣]{4}", prefix) and sum(
                1 for o in stated if o.row == s.row and _norm(o.label)[:4] == prefix
            ) == 1:
                names.add(prefix)
            # 「average waiting time」 read in Korean text as 「평균 대기」
            if _AVERAGE.search(s.label) and not re.search(r"[가-힣]", s.label):
                lowered = s.label.lower()
                names.update("평균" + ko for en, ko in _KO_MEASURE if en in lowered)
            self.labels[(s.row, s.label)] = [p for n in names if (p := _pattern(n))]

    def rows_in(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, row) of rows named in text, overlaps dropped."""
        hits = sorted(
            ((m.start(), m.end(), row) for row, patterns in self.rows.items()
             for p in patterns for m in p.finditer(text)),
            key=lambda h: (h[0], -(h[1] - h[0])),
        )
        out: list[tuple[int, int, str]] = []
        for hit in hits:
            if out and hit[0] < out[-1][1]:
                continue
            out.append(hit)
        return out

    def label_at(self, row: str, label: str, text: str) -> re.Match[str] | None:
        for pattern in self.labels.get((row, label), []):
            if m := pattern.search(text):
                return m
        return None


def _shown(written: str, s: Stated) -> str:
    """The computed value in the way the text writes numbers: 「78000」 as 「78,000」."""
    if "," not in written:
        return s.text
    places = len(s.text.split(".", 1)[1]) if "." in s.text else 0
    return f"{s.value:,.{places}f}"


#: reading of a figure's last sound, for the particle after it
_DIGIT_CODA = {"0": "ㅇ", "1": "ㄹ", "2": "", "3": "ㅁ", "4": "", "5": "", "6": "ㄱ",
               "7": "ㄹ", "8": "ㄹ", "9": ""}
_PARTICLE = re.compile(r"(으로|로|은|는|이|가|을|를|과|와)(?=[\s,.)」'\"*]|$)")
_PAIRS = {"은": ("은", "는"), "는": ("은", "는"), "이": ("이", "가"), "가": ("이", "가"),
          "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와"),
          "으로": ("으로", "로"), "로": ("으로", "로")}


def _coda(number: str) -> str:
    digits = number.replace(",", "").lstrip("-")
    if "." in digits or digits == "0" or not digits.endswith("0"):
        return _DIGIT_CODA[digits[-1]]
    zeros = len(digits) - len(digits.rstrip("0"))
    return {1: "ㅂ", 2: "ㄱ"}.get(zeros, "ㄴ")


def _particle(number: str, particle: str) -> str:
    """`particle` agreeing with how `number` is read (「3.00으로」, 「4.75로」)."""
    coda = _coda(number)
    closed, opened = _PAIRS[particle]
    if particle in ("으로", "로"):
        return opened if coda in ("", "ㄹ") else closed
    return closed if coda else opened


def _same(written: str, value: float) -> bool:
    """Whether written equals value at its shown precision."""
    written = written.replace(",", "")
    places = len(written.split(".", 1)[1]) if "." in written else 0
    if not places and abs(value) < 100:
        return abs(float(written) - value) < 1e-9
    return abs(float(written) - value) <= 0.5 * 10 ** -places + 1e-9


def _first_number(text: str, start: int, end: int) -> re.Match[str] | None:
    for m in _NUMBER.finditer(text, start, end):
        if not _OTHER_QUANTITY.match(text, m.end()):
            return m
    return None


#: between a row and its value when the label is the title's: 「SRTF: 0.50」 「SRTF는 0.50」
_RIGHT_AFTER = re.compile(r"[\s:：=\-–—|*]{0,4}(?:은|는|이|가)?[\s:：=*]{0,3}")


@dataclass(slots=True)
class Clash:
    start: int
    end: int
    written: str
    stated: Stated


def _clashes(text: str, context: str, names: _Names, stated: list[Stated]) -> list[Clash]:
    """Numbers in text that differ from the computed value they name."""
    hits = names.rows_in(text)
    if hits:
        segments = [(h[1], hits[i + 1][0] if i + 1 < len(hits) else len(text), h[2])
                    for i, h in enumerate(hits)]
    else:
        # no row named: use the title's row
        in_title = {row for _, _, row in names.rows_in(context)}
        if len(in_title) == 1:
            segments = [(0, len(text), in_title.pop())]
        else:
            # row-less values matched by label
            segments = [(0, len(text), "")] if any(not s.row for s in stated) else []
    out: list[Clash] = []
    for start, end, row in segments:
        mine = [s for s in stated if s.row == row]
        piece = text[start:end]
        found = [(m, s) for s in mine if (m := names.label_at(row, s.label, piece))]
        if found:
            m, s = min(found, key=lambda pair: pair[0].start())
            following = [other.start() for other, _ in found if other.start() > m.start()]
            number = _first_number(text, start + m.end(),
                                   start + min(following, default=len(piece)))
            if number is None:
                # value before its label
                number = _first_number(text, start, start + m.start())
        else:
            # one label for the whole line, else for the title (「SRTF: 0.50」 under
            # 「알고리즘별 평균 대기시간」)
            named = [s for s in mine if names.label_at(row, s.label, text)]
            number = _first_number(text, start, end)
            if not named and hits and number is not None and _RIGHT_AFTER.fullmatch(
                    text[start:number.start()]):
                named = [s for s in mine if names.label_at(row, s.label, context)]
            if len(named) != 1:
                continue
            s = named[0]
        if number and not _same(number.group(0), s.value):
            out.append(Clash(number.start(), number.end(), number.group(0), s))
    return out


def _mend(text: str, clashes: list[Clash]) -> str:
    for clash in sorted(clashes, key=lambda c: c.start, reverse=True):
        shown = _shown(clash.written, clash.stated)
        end = clash.end
        if m := _PARTICLE.match(text, end):
            shown += _particle(shown, m.group(1))
            end = m.end()
        text = text[: clash.start] + shown + text[end:]
    return text


#: sentence end in notes; not a decimal point
_SENTENCE_END = re.compile(r"(?<=[.!?。])(\s{1,8})")


def _check_text(text: str, context: str, names: _Names, stated: list[Stated],
                found: list[str]) -> str:
    out = []
    for line in (text or "").splitlines(keepends=True):
        # split keeping separators (odd items) to rejoin whole
        for i, piece in enumerate(_SENTENCE_END.split(line)):
            if i % 2 or not piece:
                out.append(piece)
                continue
            clashes = _clashes(piece, context, names, stated)
            found.extend(f"「{c.written}」 → {c.stated.text} ({c.stated.row} {c.stated.label})"
                         for c in clashes)
            out.append(_mend(piece, clashes))
    return "".join(out)


def _check_rows(rows: list[list[Any]], names: _Names, stated: list[Stated],
                found: list[str], context: str = "") -> list[list[Any]]:
    """Table with computed rows/labels as headers; a two-column table may take its label
    (「| SRTF | 0.50 |」 under 「평균 대기시간」) or its row (「| 평균 대기시간 | 0.50 |」 under
    「SRTF」) from `context`, the title over it."""
    if len(rows) < 2:
        return rows
    header = [str(c) for c in rows[0]]
    out = [list(rows[0])]

    def one_row(cell: str) -> str | None:
        named = {row for _, _, row in names.rows_in(cell)}
        return named.pop() if len(named) == 1 else None

    def label_of(row: str, cell: str) -> Stated | None:
        matched = [s for s in stated if s.row == row and names.label_at(row, s.label, cell)]
        return matched[0] if len(matched) == 1 else None

    by_column = [one_row(h) for h in header]
    pair = len(header) == 2 and bool(context)
    titled = one_row(context) if pair else None
    for raw in rows[1:]:
        cells = [str(c) for c in raw]
        own = one_row(cells[0]) if cells else None
        fixed = list(raw)
        for j in range(1, len(cells)):
            if own is not None:
                s = label_of(own, header[j]) if j < len(header) else None
                if s is None and pair:
                    s = label_of(own, context)
            elif j < len(by_column) and by_column[j] is not None:
                s = label_of(by_column[j], cells[0])
            elif titled is not None:
                s = label_of(titled, cells[0])
            else:
                s = None
            if s is None:
                continue
            number = _first_number(cells[j], 0, len(cells[j]))
            if number is None:
                # 「최소」 in place of the value; a figure of another kind (「+9.4%」) stands
                if cells[j].strip() and not re.search(r"\d", cells[j]):
                    found.append(f"「{cells[j]}」 → {s.text} ({s.row} {s.label})")
                    fixed[j] = s.text
            elif not _same(number.group(0), s.value):
                found.append(f"「{number.group(0)}」 → {s.text} ({s.row} {s.label})")
                fixed[j] = (cells[j][: number.start()] + _shown(number.group(0), s)
                            + cells[j][number.end():])
        out.append(fixed)
    return out


#: word fields that aren't the slide's claims
_SKIP = frozenset({"id", "layout", "title", "accent", "image", "diagram", "textScale", "number",
                   "rows", "notes", "chart"})


def check_slides(slides: list[dict[str, Any]], block: str) -> tuple[list[int], list[str]]:
    """Mend slide numbers that differ from computed values: (touched indices, corrections)."""
    stated = stated_values(block)
    if not stated:
        return [], []
    names = _Names(stated)
    touched: list[int] = []
    corrected: list[str] = []
    for index, slide in enumerate(slides):
        found: list[str] = []
        title = str(slide.get("title") or "")

        def walk(value: Any, context: str, inner: bool = False,
                 found: list[str] = found) -> Any:
            if isinstance(value, str):
                return _check_text(value, context, names, stated, found)
            if isinstance(value, list):
                if not inner:
                    return [walk(v, context, True) for v in value]
                # pair halves read each other as context
                return [walk(v, " ".join([context, *(str(o) for j, o in enumerate(value)
                                                    if j != i and isinstance(o, str))]), True)
                        for i, v in enumerate(value)]
            if isinstance(value, dict):
                return {k: walk(v, context, True) for k, v in value.items()}
            return value

        mended = dict(slide)
        for key, value in slide.items():
            if key not in _SKIP:
                mended[key] = walk(value, title)
        if isinstance(slide.get("notes"), str):
            mended["notes"] = _check_text(slide["notes"], title, names, stated, found)
        if isinstance(slide.get("rows"), list) and all(isinstance(r, list) for r in slide["rows"]):
            mended["rows"] = _check_rows(slide["rows"], names, stated, found, context=title)
        if isinstance(slide.get("chart"), dict):
            mended["chart"] = check_chart(slide["chart"], title, names, stated, found)
        if found:
            slides[index] = mended
            touched.append(index)
            corrected.extend(f"{index + 1}장 {item}" for item in found)
    return touched, corrected


#: a printed section header: 「=== SRTF ===」 「--- RR (q=2) ---」 「## FCFS」 「[SJF]」 「SRTF:」
_DECORATED = re.compile(
    r"^(?:[=\-*#~]{2,}|#{1,6}(?=\s)|\[)\s*(?P<name>[^\n]{1,40}?)\s*(?:[=\-*#~]{2,}|\])?\s*:?$")
_COLON_HEADER = re.compile(r"^(?P<name>[^:：\n]{1,40}?)\s*[:：]$")
#: 「label: value」 or 「label = value」 with one number and an optional unit
_PRINTED = re.compile(
    r"^(?P<label>[^\n]{1,80}?)\s*[:=：]\s*(?P<num>-?\d[\d,]{0,15}(?:\.\d{1,10})?)"
    r"\s*(?P<unit>[^\d\s,|:=]{0,8})\s*$")
#: words that open a measure in a label: what precedes them names the row
_MEASURE = re.compile(r"평균|합계|총\s{0,2}[가-힣]|(?<![A-Za-z])(?:average|avg|mean|total)"
                      r"(?![A-Za-z])", re.I)


def _value_match(line: str) -> re.Match[str] | None:
    """「label: value」 whose label closes its brackets (「RR (q=2)」 is a name, not q = 2)."""
    m = _PRINTED.match(line)
    if not m:
        return None
    label = m.group("label")
    if label.count("(") + label.count("（") != label.count(")") + label.count("）"):
        return None
    return m


def _printed_line(line: str, section: str) -> Stated | None:
    m = _value_match(line)
    if not m:
        return None
    label = m.group("label").strip().strip("-*•·").strip()
    if (not label or re.fullmatch(r"[\d\s.,:=-]*", label) or len(_NUMBER.findall(label)) > 2
            or re.search(r"[=:：]\s*-?\d", _PAREN.sub("", label))):
        return None
    number = m.group("num").replace(",", "")
    row = section
    if not row:
        trailing = re.search(r"[(（]([^()（）]{1,30})[)）]\s*$", label)
        measure = _MEASURE.search(label)
        if trailing and measure and measure.start() < trailing.start():
            # 「Average waiting time (SRTF)」
            row, label = trailing.group(1).strip(), label[: trailing.start()].strip()
        elif measure and measure.start() > 0:
            # 「SRTF 평균 대기시간」 「FCFS average waiting time」
            head = label[: measure.start()].strip(" -:|")
            if head:
                row, label = head, label[measure.start():].strip()
    return Stated(row, label, number, float(number))


#: one 「label = value」 of several on a line: 「RR TQ2 avg wait=5.25 avg resp=4.75」
_PAIR = re.compile(
    r"(?P<label>[^=:：,;\n]{1,80}?)\s*[=:：]\s*(?P<num>-?\d[\d,]{0,15}(?:\.\d{1,10})?)"
    r"(?P<unit>%|[A-Za-z가-힣]{1,4})?(?=\s|[,;]|$)")


def _pairs(line: str, section: str) -> list[Stated] | None:
    """Values of a line holding two or more 「label = value」 pairs, each its own; the first
    pair's row names the rest (「RR TQ2」 in 「RR TQ2 avg wait=5.25 avg resp=4.75」). `None`
    for a line of one value."""
    matches = list(_PAIR.finditer(line))
    if len(matches) < 2 or not line.strip().endswith(matches[-1].group(0).strip()):
        return None
    out: list[Stated] = []
    head = line[: matches[0].start()].strip()
    # 「SRTF: wait=3.00, turnaround=7.25」
    row = head[:-1].strip() if head.endswith((":", "：")) and head[:-1].strip() else section
    for m in matches:
        piece = m.group(0).strip(" ,;")
        s = _printed_line(piece, row)
        if s is None:
            continue
        if not row and s.row:
            row = s.row
        elif row and not s.row:
            s = Stated(row, s.label, s.text, s.value)
        out.append(s)
    return out


def printed_values(outputs: list[str]) -> list[Stated]:
    """Single-number values code printed, by row and label; a later run's value for the same
    quantity replaces an earlier one (the run that corrected it)."""
    found: dict[tuple[str, str], Stated] = {}
    for output in outputs:
        section = ""
        for raw in (output or "").splitlines():
            line = raw.strip()
            if not line or line.lower() in ("stdout:", "stderr:"):
                continue
            if "|" in line and "=" in line:
                stated = stated_values(line)
            elif (head := _DECORATED.match(line)) and not _value_match(
                    line.strip("=-*#~[] ")):
                section = head.group("name").strip().rstrip(":：").strip()
                continue
            elif (head := _COLON_HEADER.match(line)) and not _NUMBER.search(
                    _PAREN.sub("", head.group("name"))):
                section = head.group("name").strip()
                continue
            elif (several := _pairs(line, section)) is not None:
                stated = several
            else:
                stated = [s] if (s := _printed_line(line, section)) else []
            for s in stated:
                key = (_norm(s.row), _norm(s.label))
                found.pop(key, None)
                found[key] = s
    return list(found.values())


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<h>.+?)\s*#*$")
_RULE_CELL = re.compile(r":?-{2,}:?")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def check_markdown(text: str, stated: list[Stated]) -> tuple[str, list[str]]:
    """A markdown answer's numbers that name a printed value set to it: sentences read with
    the nearest heading as their title, tables cell by cell. `(text, corrections)`."""
    # a value printed with no row (「합계: 152760」) is too loose to pin a line of prose to
    stated = [s for s in stated if s.row]
    if not stated or not text:
        return text, []
    names = _Names(stated)
    found: list[str] = []
    lines = text.split("\n")
    out: list[str] = []
    heading = ""
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if fenced or line.lstrip().startswith("```"):
            out.append(line)
            i += 1
            continue
        if line.lstrip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.extend(_check_table(block, heading, names, stated, found))
            continue
        if m := _HEADING.match(line):
            heading = m.group("h")
            out.append(line)
            i += 1
            continue
        out.append(_check_text(line, heading, names, stated, found))
        i += 1
    return "\n".join(out), found


def _check_table(block: list[str], heading: str, names: _Names, stated: list[Stated],
                 found: list[str]) -> list[str]:
    rows = [_cells(line) for line in block]
    rule = [j for j, cells in enumerate(rows) if cells and all(
        _RULE_CELL.fullmatch(c) for c in cells if c)]
    body = [cells for j, cells in enumerate(rows) if j not in rule]
    if len(body) < 2:
        return block
    before = [list(r) for r in body]
    checked = _check_rows(body, names, stated, found, context=heading)
    out, k = [], 0
    for j, line in enumerate(block):
        if j in rule:
            out.append(line)
            continue
        new = [str(c) for c in checked[k]]
        out.append(line if new == before[k] else "| " + " | ".join(new) + " |")
        k += 1
    return out


def check_chart(chart: dict[str, Any], title: str, names: _Names, stated: list[Stated],
                found: list[str]) -> dict[str, Any]:
    """Chart bars naming a computed value set to it: a category or series names the row, the
    other (or the title) the value's label."""
    categories = [str(c) for c in chart.get("categories") or []]
    series = [s for s in chart.get("series") or [] if isinstance(s, dict)]
    out = {**chart, "series": [dict(s) for s in series]}

    def one(text: str) -> str | None:
        rows = {row for _, _, row in names.rows_in(text)}
        return rows.pop() if len(rows) == 1 else None

    def label_of(row: str, *texts: str) -> Stated | None:
        for text in texts:
            matched = [s for s in stated if s.row == row and names.label_at(row, s.label, text)]
            if len(matched) == 1:
                return matched[0]
        return None

    for item in out["series"]:
        values = list(item.get("values") or [])
        series_row = one(str(item.get("name") or ""))
        for j, category in enumerate(categories[: len(values)]):
            row = one(category)
            if row is not None:
                s = label_of(row, str(item.get("name") or ""), title)
            elif series_row is not None:
                s = label_of(series_row, category)
            else:
                s = None
            if s is None:
                continue
            try:
                value = float(values[j])
            except (TypeError, ValueError):
                continue
            if not _same(f"{value:g}", s.value):
                found.append(f"「{values[j]}」 → {s.text} ({s.row} {s.label})")
                values[j] = s.value
        item["values"] = values
    return out
