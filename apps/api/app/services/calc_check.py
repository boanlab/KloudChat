"""Slide numbers corrected against code-computed values; only unambiguous claims."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: slide number; letters in 「P1」「q=2」 excluded
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.,])-?(?:\d{1,3}(?:,\d{3})+|\d{1,15})(?:\.\d{1,10})?(?![\d])")
#: counts of other things: 「2배」「30%」「3장」
_OTHER_QUANTITY = re.compile(r"\s{0,2}(?:배|%|퍼센트|장|개|번|명|차|회|순위|위|등|단계|가지|줄|쪽)")
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


#: the English a table may name a Korean row by (「| Priority | 2.0 |」)
_ENGLISH = {"우선순위": re.compile(r"(?<![A-Za-z])priority(?![A-Za-z])", re.I)}
#: words picking one reading of a shared name, the non-preemptive one first (it holds 「선점」)
_READINGS = ((r"비\s{0,2}선점|non-?\s?preemptive", "비선점"),
             (r"선점|preemptive", "선점"))


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


#: a word of a sentence or a printed line, for telling which group a value is about
_WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{2,}")
#: words every printed table shares: they name no group
_COMMON_WORDS = frozenset({"평균", "전체", "합계", "비율", "점수", "응답", "문항", "기준",
                           "결과", "값"})
#: a figure that is a gap, not the level beside it: 「−0.79」, 「0.79 낮습니다」, 「차이는 0.79」
_GAP_BEFORE = re.compile(r"(?:[−\-+±]\s{0,1}|(?:차이|격차|차)(?:는|가|가\s?약)?\s{0,3})$")
_GAP_AFTER = re.compile(r"\s{0,2}(?:점|%p|%\s?포인트|포인트|p)?\s{0,2}"
                        r"(?:낮|높|차이|많|적|앞서|뒤|증가|감소|상승|하락|늘|줄|더\s|덜\s)")


#: an end of a range: 「1~2점」
_RANGE_AFTER = re.compile(r"\s{0,1}[~∼〜–]\s{0,1}\d")
_RANGE_BEFORE = re.compile(r"\d\s{0,1}[~∼〜–]\s{0,1}$")


def _a_gap(text: str, number: re.Match[str]) -> bool:
    """Whether the number is a gap (「0.79 낮다」) or a range's end, not a value."""
    return bool(_GAP_BEFORE.search(text[max(0, number.start() - 8):number.start()])
                or _GAP_AFTER.match(text, number.end())
                or _RANGE_AFTER.match(text, number.end())
                or _RANGE_BEFORE.search(text[max(0, number.start() - 4):number.start()]))


def _stem_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text or "") if w not in _COMMON_WORDS}


class _Names:
    """Slide spellings of each row and label."""

    #: the code's printed lines: a written value printed beside a group the sentence names
    #: is that group's value, not a slip for the stated one
    printed: tuple[str, ...] = ()
    #: mark a clash instead of setting it to the printed value (`check_markdown(mark=True)`)
    mark: bool = False
    #: every printed value by row (`check_markdown(keep_row_values=True)`): a written value
    #: the code printed for the row (another label's) is that row's figure, never a slip
    by_row: dict[str, list[float]] | None = None

    def rows_value(self, row: str, written: str) -> bool:
        if self.by_row is None:
            return False
        try:
            value = float(written.replace(",", ""))
        except ValueError:
            return False
        if any(abs(value - v) < 1e-9 for v in (self.by_row or {}).get(row, ())):
            return True
        # printed on a line that names the row (「사회과학대학 n=24, 전반만족 4.17」), in any
        # spelling (「58,277,000」 for a printed 「58277000.0」)
        if row and any(row in line and self._on(written, line) for line in self.printed):
            return True
        # a value of five or more digits the code printed anywhere is the code's, not a slip:
        # marked, it would doubt the code's own figure
        return self.mark and len(re.sub(r"\D", "", written).lstrip("0")) >= 5 and any(
            self._on(written, line) for line in self.printed)

    @staticmethod
    def _on(written: str, line: str) -> bool:
        return any(_same(written, float(m.group(0).replace(",", "")))
                   for m in _NUMBER.finditer(line))

    def vouched(self, written: str, sentence: str, s: Stated) -> bool:
        """Whether `written` is printed on a line naming a group the sentence also names
        (「예술대학 … 2.89」 printed, 「예술대학 시설 평균 2.89점」 written) other than `s`'s."""
        own = _stem_words(f"{s.row} {s.label}")
        words = {w for w in _stem_words(sentence)
                 if not any(w.startswith(o) or o.startswith(w) for o in own)}
        if not words:
            return False
        for line in self.printed:
            if not any(_same(written, float(m.group(0).replace(",", "")))
                       for m in _NUMBER.finditer(line)):
                continue
            named = _stem_words(line)
            if any(w.startswith(n) or n.startswith(w) for w in words for n in named
                   if min(len(w), len(n)) >= 2):
                return True
        return False

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
            if core and cores.count(core) == 1 and (english := _ENGLISH.get(core)):
                patterns.append(english)
            self.rows[row] = patterns
        #: a bare name shared by several rows that differ only in a word (「우선순위」 for
        #: 「우선순위(비선점)」 and 「우선순위(선점)」), read in a table row by its value
        self.groups: list[tuple[list[re.Pattern[str]], list[str]]] = []
        for core in dict.fromkeys(c for c in cores if c and cores.count(c) > 1):
            members = [r for r, c in zip(rows, cores, strict=True) if c == core]
            if any(re.search(r"\d", r) for r in members) or not (bare_core := _pattern(core)):
                continue
            self.groups.append(([bare_core, *filter(None, [_ENGLISH.get(core)])], members))
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

    def group_in(self, cell: str) -> list[str]:
        """The rows a bare shared name in `cell` may mean, narrowed by a word naming one
        (「비선점」/「non-preemptive」 → 「(비선점)」); `[]` when none is named."""
        for patterns, members in self.groups:
            if not any(p.search(cell) for p in patterns):
                continue
            for word, tag in _READINGS:
                if re.search(word, cell, re.I):
                    picked = [r for r in members
                              if _norm("".join(_PAREN.findall(r))) == tag]
                    return picked or members
            return members
        return []

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


#: what joins two row names that share the values after them: 「자연과학·공학」 「A와 B」
_JOINED = re.compile(r"\s{0,2}(?:[·,/]|와|과|및|그리고)\s{0,2}")


def _numbers(text: str, start: int, end: int) -> list[re.Match[str]]:
    return [m for m in _NUMBER.finditer(text, start, end)
            if not _OTHER_QUANTITY.match(text, m.end())]


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
    shared = False
    for start, end, row in segments:
        mine = [s for s in stated if s.row == row]
        piece = text[start:end]
        if shared:
            # 「자연과학·공학은 좌석 부족(각 22·41건)」: the values are the rows' together
            shared = bool(_JOINED.fullmatch(piece))
            continue
        if _JOINED.fullmatch(piece) and hits:
            shared = True
            continue
        found = [(m, s) for s in mine if (m := names.label_at(row, s.label, piece))]
        if found:
            m, s = min(found, key=lambda pair: pair[0].start())
            following = [other.start() for other, _ in found if other.start() > m.start()]
            stop = start + min(following, default=len(piece))
            if len(_numbers(text, start, stop)) >= 2:
                # 「41건이 좌석 부족을, 31건이 와이파이를」: which value is the label's is unclear
                continue
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
            if _a_gap(text, number) or names.vouched(number.group(0), text, s):
                continue
            out.append(Clash(number.start(), number.end(), number.group(0), s))
    return out


#: what a clash gets under `mark`: the written value stays, marked
_MARK = "(확인 필요)"


def _mend(text: str, clashes: list[Clash], mark: bool = False) -> str:
    for clash in sorted(clashes, key=lambda c: c.start, reverse=True):
        if mark:
            if text[clash.end:clash.end + len(_MARK) + 1].lstrip().startswith(_MARK):
                continue
            # after the unit: 「24점(확인 필요)」, never between a value and its unit
            end = clash.end
            if m := re.match(r"[%점명건개원배시간분초]{1,2}", text[end:]):
                end += m.end()
            text = text[:end] + _MARK + text[end:]
            continue
        shown = _shown(clash.written, clash.stated)
        end = clash.end
        # a value set from a known run drops an earlier pass's mark (「4.70(확인 필요)」 → 「4.75」)
        if m := re.match(r"[%점명건개원배시간분초]{0,2}" + re.escape(_MARK), text[end:]):
            text = text[:end + m.end() - len(_MARK)] + text[end + m.end():]
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
            clashes = [c for c in _clashes(piece, context, names, stated)
                       if not names.rows_value(c.stated.row, c.written)]
            if names.mark:
                found.extend(f"「{c.written}」 확인 필요 (코드 값 {c.stated.text}: "
                             f"{c.stated.row} {c.stated.label})" for c in clashes)
            else:
                found.extend(f"「{c.written}」 → {c.stated.text} ({c.stated.row} {c.stated.label})"
                             for c in clashes)
            out.append(_mend(piece, clashes, names.mark))
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
        shared = names.group_in(cells[0]) if cells and own is None else []
        if len(shared) == 1:
            own = shared[0]
        elif shared:
            # 「| Priority | 2.0 |」: read as the reading most of its values agree with (the
            # first on a tie); a value it doesn't hold is set to it and the row says which
            # reading it is
            columns = [(j, number) for j in range(1, min(len(cells), len(header)))
                       if (number := _first_number(cells[j], 0, len(cells[j]))) is not None]

            def agree(row: str, columns=columns) -> int:
                return sum(1 for j, number in columns
                           if (s := label_of(row, header[j])) and _same(number.group(0), s.value))

            best = max(shared, key=agree)
            for j, number in columns:
                s = label_of(best, header[j])
                if s is None or _same(number.group(0), s.value) \
                        or names.rows_value(s.row, number.group(0)):
                    continue
                if names.mark:
                    if _MARK not in cells[j]:
                        found.append(f"「{cells[0]} {number.group(0)}」 확인 필요 (코드 값 "
                                     f"{s.text}: {s.row} {s.label})")
                        fixed[j] = f"{cells[j].rstrip()}{_MARK}"
                    continue
                found.append(f"「{cells[0]} {number.group(0)}」 → {s.row} {s.text} ({s.label})")
                fixed[j] = (cells[j][: number.start()] + _shown(number.group(0), s)
                            + cells[j][number.end():])
                fixed[0] = s.row
            out.append(fixed)
            continue
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
            elif not _same(number.group(0), s.value) \
                    and not names.rows_value(s.row, number.group(0)):
                if names.mark:
                    if _MARK in cells[j]:
                        continue
                    found.append(f"「{number.group(0)}」 확인 필요 (코드 값 {s.text}: "
                                 f"{s.row} {s.label})")
                    fixed[j] = f"{cells[j].rstrip()}{_MARK}"
                    continue
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


#: a printed table row: a name, then numbers only (pandas: 「온라인학습 3.56  4.0  0.83  10.8」)
_TABLE_ROW = re.compile(
    r"^\s*(?P<name>[^\d\s|=:：][^\d|=:：]{0,40}?)\s+(?P<values>-?\d[\d\s.,%()-]*)$")


def _tabled_apart(s: Stated, printed: tuple[str, ...]) -> bool:
    """Whether the code also printed `s`'s row as a table row without `s`'s value: which
    group a value written beside that name is for is then unclear, and none is set to `s`."""
    own = _norm(s.row)
    for line in printed:
        m = _TABLE_ROW.match(line)
        if m is None or _norm(m.group("name")) != own:
            continue
        values = [float(n.replace(",", "")) for n in _NUMBER.findall(m.group("values"))]
        if values and not any(abs(v - s.value) < 1e-9 for v in values):
            return True
    return False


def check_markdown(text: str, stated: list[Stated],
                   printed: list[str] | None = None, *, mark: bool = False,
                   keep_row_values: bool = False) -> tuple[str, list[str]]:
    """A markdown answer's numbers that name a printed value set to it: sentences read with
    the nearest heading as their title, tables cell by cell. `(text, corrections)`. A gap
    (「0.79 낮다」) or a value `printed` beside a group the sentence names stays.

    `keep_row_values`: a value the code printed for the same row under another label is left
    alone — the binding, not the value, is then the doubt (a textbook schedule leaves it off:
    a turnaround time written as the waiting time is a slip). `mark`: a clash is marked
    「(확인 필요)」 where it stands, never rewritten."""
    lines_printed = tuple(line for out in printed or () for line in str(out).splitlines()
                          if line.strip())
    # a value printed with no row (「합계: 152760」) is too loose to pin a line of prose to;
    # a row the code also printed as a table row of other values is one group's of several
    stated = [s for s in stated if s.row and not _tabled_apart(s, lines_printed)]
    if not stated or not text:
        return text, []
    names = _Names(stated)
    names.printed = lines_printed
    names.mark = mark
    if keep_row_values or mark:
        names.by_row = {}
        for one in stated:
            names.by_row.setdefault(one.row, []).append(one.value)
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
