"""The same labelled quantity two source files give differently, and a document stating one.

A proposal says 「총 사업비 3.5억 원」 and the minutes 「총사업비는 3.2억 원」; a report that
writes 「3.2억 원 … 이 금액을 기준으로 진행합니다」 has settled what its sources leave open. The
check reads every labelled, unit-bearing quantity of each source file, finds the labels two files
give different values for, and marks the document that states one of them without the other:
both values, where each comes from, and a named blank for the decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import source_focus

#: scale words of a money amount, in won
_SCALE = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4, "천": 10**3, "": 1}
#: a quantity: number, then a scale and/or a unit. Day, month, year and clock units are no
#: quantities (they are dates), and a bare number says nothing about what it counts.
_VALUE = (r"(?P<num>\d[\d,]*(?:\.\d+)?)\s{0,1}"
          r"(?:(?P<money>조|억|천만|백만|만|천)?\s{0,1}원|(?P<scale>조|억|천만|백만|만)(?![가-힣])"
          r"|(?P<pct>%|퍼센트)|(?P<count>명|개사|개월|개|건|곳|대|회|배|점)(?![가-힣]))")
_QUANTITY = re.compile(_VALUE)
#: what stands between a label and its value
_GAP = re.compile(r"[\s|:：=]*(?:약|총|합계|대략)?\s{0,1}$")
_JOSA = re.compile(r"(?:으로|[은는이가도만의을를로])$")
_TOKEN = re.compile(r"([가-힣A-Za-z]{1,15})\s{0,1}$")


@dataclass(frozen=True, slots=True)
class Quantity:
    keys: frozenset[str]
    kind: str
    value: float
    #: the amount as the source writes it
    shown: str


@dataclass(frozen=True, slots=True)
class Conflict:
    #: the label as the first source writes it
    label: str
    #: (amount as written, file) per distinct value, in the order the files come
    values: tuple[tuple[str, str], ...]
    keys: frozenset[str]
    kind: str


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _labels(before: str) -> frozenset[str]:
    """What names the amount that follows `before`: the word right before it, without its
    particle, and with a short word before it (「총 사업비」) joined on."""
    before = _GAP.sub("", before)
    found = _TOKEN.search(before)
    if not found:
        return frozenset()
    word = found.group(1)
    keys = {word}
    if (bare := _JOSA.sub("", word)) and len(bare) >= 2:
        keys.add(bare)
        word = bare
    head = before[:found.start()].rstrip()
    if (prev := _TOKEN.search(head)) and len(prev.group(1)) <= 2 and head.endswith(prev.group(1)):
        keys.add(prev.group(1) + word)
    # a label is a Korean word (an English 「the 95%」 names nothing)
    return frozenset(k for k in keys if len(k) >= 3 and re.search(r"[가-힣]", k))


def quantities(text: str) -> list[Quantity]:
    """Every labelled, unit-bearing quantity of `text`."""
    out: list[Quantity] = []
    for m in _QUANTITY.finditer(text or ""):
        num = _number(m.group("num"))
        if m.group("pct"):
            kind, value = "pct", num
        elif m.group("count"):
            kind, value = f"count:{m.group('count')}", num
        else:
            scale = m.group("money") or m.group("scale") or ""
            kind, value = "money", num * _SCALE[scale]
        keys = _labels(text[max(0, m.start() - 40):m.start()])
        if keys:
            out.append(Quantity(keys, kind, value, m.group(0).strip().rstrip(".,)")))
    return out


def source_files(blocks: list[str]) -> list[tuple[str, str]]:
    """`(file name, text)` of every file in the material blocks (「## name」 parts)."""
    out: list[tuple[str, str]] = []
    for block in blocks:
        parts = source_focus.file_parts(block)
        out.extend((part.name, part.text) for part in parts)
    return out


def conflicts(files: list[tuple[str, str]]) -> list[Conflict]:
    """Labels two or more files give different values for (money, percentages or counts of the
    same unit, within a factor of two of each other: another size is another thing). A label a
    file gives several values for is a series there, not one quantity, and is left alone."""
    per: dict[str, dict[str, list[Quantity]]] = {}
    for name, text in files:
        for q in quantities(text):
            for key in q.keys:
                per.setdefault(key, {}).setdefault(name, []).append(q)
    found: list[Conflict] = []
    seen: set[frozenset[tuple[str, float]]] = set()
    for key, by_file in per.items():
        picked: list[tuple[str, Quantity]] = []
        kind = ""
        for name, qs in by_file.items():
            qs = [q for q in qs if key in q.keys]
            if len({(q.kind, q.value) for q in qs}) != 1:
                picked = []
                break
            kind = kind or qs[0].kind
            if qs[0].kind != kind:
                picked = []
                break
            picked.append((name, qs[0]))
        if len(picked) < 2:
            continue
        values = {q.value for _, q in picked}
        if len(values) < 2 or max(values) > 2 * min(values):
            continue
        ident = frozenset((q.kind, q.value) for _, q in picked)
        if ident in seen:
            continue
        seen.add(ident)
        distinct: dict[float, tuple[str, str]] = {}
        for name, q in picked:
            distinct.setdefault(q.value, (q.shown, name))
        found.append(Conflict(key, tuple(distinct.values()), frozenset({key}), kind))
    return found


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def stated(text: str, conflict: Conflict) -> list[Quantity]:
    """The quantities `text` gives under the conflict's label."""
    return [q for q in quantities(text) if q.kind == conflict.kind and q.keys & conflict.keys]


def unreported(sections: list[dict], found: list[Conflict],
               reference_heading: re.Pattern[str]) -> list[Conflict]:
    """Conflicts whose label the document states a value for without giving every value."""
    body = "\n".join(str(s.get("content") or "") for s in sections
                     if not reference_heading.search(str(s.get("heading") or "")))
    flat = _flat(body)
    out = []
    for conflict in found:
        if not stated(body, conflict):
            continue
        given = {q.value for q in quantities(body) if q.kind == conflict.kind}
        if all(_wins(shown, flat) or _number_of(shown, conflict.kind) in given
               for shown, _ in conflict.values):
            continue
        out.append(conflict)
    return out


def _number_of(shown: str, kind: str) -> float | None:
    found = quantities(f"항목 {shown}")
    return found[0].value if found and found[0].kind == kind else None


def _wins(shown: str, flat: str) -> bool:
    return _flat(shown) in flat


def note(conflict: Conflict) -> str:
    """The sentence that reports a conflict: both values, their files, the decision left open."""
    label = conflict.label
    given = ", ".join(f"{name}은(는) {shown}" for shown, name in conflict.values)
    return (f"※ 확인 필요: {label}은(는) 자료마다 값이 다릅니다 ({given}). "
            f"기준 금액은 [{label} 확정 필요]입니다.")


_SENTENCE_END = re.compile(r"(?<=[.!?다요])(?=\s|$)")


def mark(sections: list[dict], found: list[Conflict],
         reference_heading: re.Pattern[str]) -> tuple[list[dict], list[Conflict]]:
    """The note after the paragraph (or table) that first states each unreported conflict's
    label; the conflicts marked."""
    out = [dict(s) for s in sections]
    marked: list[Conflict] = []
    for conflict in unreported(out, found, reference_heading):
        for section in out:
            if reference_heading.search(str(section.get("heading") or "")):
                continue
            lines = str(section.get("content") or "").split("\n")
            at = next((i for i, line in enumerate(lines) if stated(line, conflict)), None)
            if at is None:
                continue
            end = at
            if lines[at].lstrip().startswith("|"):
                while end + 1 < len(lines) and lines[end + 1].lstrip().startswith("|"):
                    end += 1
                lines[end + 1:end + 1] = ["", note(conflict)]
            else:
                lines[at] = f"{lines[at].rstrip()} {note(conflict)}"
            section["content"] = "\n".join(lines)
            marked.append(conflict)
            break
    return out, marked


__all__ = ["Conflict", "Quantity", "conflicts", "mark", "note", "quantities", "source_files",
           "stated", "unreported"]
