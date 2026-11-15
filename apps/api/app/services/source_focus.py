"""The project file a request names as what it reads: 「11월 10일 회의록」, 「회의록_1110.docx」.

A project hands the writer every file at once; the head of a minutes document, its meeting day
and the days its action items count from belong to the one meeting the request names, never to
the first dated meeting line among twelve files. Pure functions over the material text, whose
files arrive as 「## <file name>」 parts (`workspace_context._knowledge_block`).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

#: the project knowledge block's header (`workspace_context._project_knowledge`)
KNOWLEDGE_HEADER = "# 프로젝트 지식"
#: a file's heading inside a material block: 「## 2026-11-10 주간회의록.docx」
_FILE_HEADING = re.compile(r"^#{2}\s{1,3}(\S[^\n]{0,200}?)\s*$")
_HAS_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}(?:\s|$)")
#: a top-level block header ends the file before it
_TOP_HEADER = re.compile(r"^#\s{1,3}\S")
#: the day a request names a source document by: 「11월 10일 회의록」, 「11/10 회의」,
#: 「2026-11-10 미팅 메모」, 「11월 10일에 열린 운영회의 회의록」
_NAMED_DAY = re.compile(
    r"(?<![\d./-])(?:(?P<y>20\d{2})\s{0,2}(?:년|[-./])\s{0,2})?(?P<m>\d{1,2})\s{0,2}(?:월\s{0,2}|[/.-])"
    r"\s{0,2}(?P<d>\d{1,2})(?![\d/])"
    r"(?!\s{0,2}일?\.?(?:\s{0,2}[(（][월화수목금토일][)）])?\s{0,2}(?:까지|마감|전에|이전|이후|부터))"
    r"\s{0,2}일?\.?(?:\s{0,2}[(（][월화수목금토일][)）])?\s{0,2}(?:에\s{0,2})?(?:(?:있었던|열린|진행한|진행된|했던|한)\s{0,2})?"
    r"(?:[가-힣A-Za-z0-9]{1,12}\s{0,2}){0,2}?"
    r"(?P<kind>회의록|회의|미팅|면담|간담회|모임|위원회|세미나|녹취|녹음|메모|보고서|자료|일지)")
_MEETING_KIND = re.compile(r"회의|미팅|면담|간담회|모임|위원회|세미나|녹취|녹음|메모")
#: a kind naming the record of something held: its day has passed
RECORD_KIND = re.compile(r"회의록|녹취|녹음|메모|일지")
#: where a file states its own day: name and opening lines
_HEAD_CHARS = 900


@dataclass(frozen=True, slots=True)
class FilePart:
    name: str
    #: the part's text with its heading
    text: str


def file_parts(material: str, names: list[str] | None = None) -> list[FilePart]:
    """The files in `material`, each from its 「## name」 heading to the next file or block;
    a heading counts when it is one of `names` or reads as a file name."""
    known = {" ".join(n.split()) for n in names or [] if n}
    lines = (material or "").split("\n")
    starts: list[tuple[int, str]] = []
    for k, line in enumerate(lines):
        if not (m := _FILE_HEADING.match(line)):
            continue
        name = " ".join(m.group(1).split())
        if name in known or any(name.startswith(n) for n in known) or _HAS_EXTENSION.search(name):
            starts.append((k, name))
    out: list[FilePart] = []
    for i, (k, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        for j in range(k + 1, end):
            if _TOP_HEADER.match(lines[j]):
                end = j
                break
        out.append(FilePart(name, "\n".join(lines[k:end]).strip()))
    return out


def _spellings(month: int, day: int) -> tuple[str, ...]:
    return (f"{month}월{day}일", f"{month:02d}월{day:02d}일", f"{month}/{day}",
            f"{month:02d}/{day:02d}",
            f"-{month:02d}-{day:02d}", f".{month:02d}.{day:02d}", f"{month}.{day}.",
            f"_{month:02d}{day:02d}", f"{month:02d}{day:02d}_", f"{month:02d}{day:02d}.",
            f"-{month}-{day}", f"{month:02d}-{day:02d}")


def _dated(text: str, month: int, day: int) -> bool:
    flat = re.sub(r"\s+", "", text or "")
    return any(re.search(re.escape(s) + r"(?!\d)", flat) for s in _spellings(month, day))


def named_day(request: str) -> tuple[int | None, int, int, str] | None:
    """(year or None, month, day, kind) of the dated document the request names, first one."""
    for m in _NAMED_DAY.finditer(request or ""):
        month, day = int(m.group("m")), int(m.group("d"))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return (int(m.group("y")) if m.group("y") else None, month, day, m.group("kind"))
    return None


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "").strip()


def requested_part(request: str, material: str, names: list[str] | None = None) -> FilePart | None:
    """The one file the request names: by its name, or by the day and kind it gives
    (「11월 10일 회의록」 → the file whose name or opening carries 11/10). None when the request
    names none, or when several files fit alike (no guessing between them)."""
    parts = file_parts(material, names)
    if len(parts) < 2:
        return None
    flat_request = re.sub(r"\s+", "", request or "")
    by_name = [p for p in parts if len(stem := re.sub(r"\s+", "", _stem(p.name))) >= 4
               and stem in flat_request]
    if len(by_name) == 1:
        return by_name[0]
    found = named_day(request)
    if found is None:
        return None
    _, month, day, kind = found
    dated = [p for p in parts
             if _dated(p.name, month, day) or _dated(p.text[:_HEAD_CHARS], month, day)]
    if len(dated) > 1 and _MEETING_KIND.search(kind):
        # a meeting's record over a file that only mentions the day
        meetings = [p for p in dated if _MEETING_KIND.search(p.name)
                    or _MEETING_KIND.search(p.text[:200])]
        dated = meetings or dated
    if len(dated) > 1:
        # the file whose own name carries the day over one whose text mentions it
        named = [p for p in dated if _dated(p.name, month, day)]
        dated = named if len(named) == 1 else dated
    return dated[0] if len(dated) == 1 else None


def without_knowledge(texts: list[str]) -> list[str]:
    """`texts` without the project knowledge block: the project's files are what a document
    draws on, and their own heads (a minutes' 작성자·일시) are no head of the document written."""
    return [t for t in texts if not (t or "").lstrip().startswith(KNOWLEDGE_HEADER)]


_FULL = re.compile(
    r"(20\d{2})\s{0,2}(?:년|[-./])\s{0,2}(\d{1,2})\s{0,2}(?:월|[-./])\s{0,2}(\d{1,2})")


def resolve_day(year: int | None, month: int, day: int, material: str,
                today: dt.date | None) -> dt.date | None:
    """A named day as a date: its own year, else the year a full date of that day in the
    material gives, else the latest such day not after today (a record names a day passed)."""
    try:
        if year:
            return dt.date(year, month, day)
        years = {int(m.group(1)) for m in _FULL.finditer(material or "")
                 if int(m.group(2)) == month and int(m.group(3)) == day}
        for m in re.finditer(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)", material or ""):
            if int(m.group(2)) == month and int(m.group(3)) == day:
                years.add(int(m.group(1)))
        if len(years) == 1:
            return dt.date(years.pop(), month, day)
        if today is None:
            return None
        found = dt.date(today.year, month, day)
        return found if found <= today else found.replace(year=today.year - 1)
    except ValueError:
        return None


__all__ = ["FilePart", "KNOWLEDGE_HEADER", "file_parts", "named_day", "requested_part",
           "resolve_day", "without_knowledge"]
