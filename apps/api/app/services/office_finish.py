"""A report's last shape before it becomes an HWP or Word file handed to its reader.

On screen a report keeps its 「[n]」 citations (the source panel opens them) and its
「(확인 필요)」 marks (the panel lists them). A file goes to a 팀장 or a 결재권자, who has
neither: in a work document the numbers are noise and the reader wants the files it was
built from; and a mark meant for the requester reads, in the file, as an unfinished draft.
So a work document's file names its sources in a short 「근거 자료」 list, and every marked
value becomes a named blank the requester fills (「[처리량]」), never a value left standing as
if checked. Academic writing keeps its numbered citations and reference list.
"""

from __future__ import annotations

import re
from typing import Any

from app.services import citations

#: documents written for work, whose reader takes the facts as the writer's own
WORK_FORMATS = frozenset({
    "minutes", "agenda", "status", "official", "result_report", "internal_draft", "gaejo",
    "event_plan", "press", "incident", "biz",
})
#: writing whose reader checks each claim against the work it cites
ACADEMIC_FORMATS = frozenset({"paper", "review", "lab", "term", "plan", "syllabus"})
EVIDENCE_HEADING = "근거 자료"


def plain_sources(format_id: str | None, *, hwp: bool, scholarly: bool) -> bool:
    """Whether the file lists its sources instead of numbering them in the text: a work
    document always; any other 한글 file unless it is academic writing."""
    fmt = format_id or ""
    if fmt in WORK_FORMATS:
        return True
    return hwp and fmt not in ACADEMIC_FORMATS and not scholarly


def _label(source: dict) -> str:
    """How the list names a source: the file it is, else its title and where it is from."""
    name = str(source.get("fileName") or "").strip()
    title = " ".join(str(source.get("title") or "").split())
    if source.get("origin") == "file" or source.get("attached"):
        return name or title
    publisher = str(source.get("publisher") or "").strip()
    return f"{title} ({publisher})" if publisher and publisher not in title else title


def _without_citations(text: str) -> str:
    def strip(piece: str) -> str:
        piece = citations._CITATION.sub("", piece)
        # 「…했다 .」 / 「문장 ,」 / a doubled space the tag left
        piece = re.sub(r"[ \t]+([.,;:!?)」』])", r"\1", piece)
        piece = re.sub(r"\(\s*\)", "", piece)
        return re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", piece)

    return citations._outside_code(text, strip)


def without_citations(sections: list[dict]) -> list[dict]:
    """`sections` with no 「[n]」 in their prose."""
    return [{**s, "content": _without_citations(str(s.get("content") or ""))}
            if citations._prose(s) else s for s in sections]


def as_source_list(sections: list[dict], sources: list[dict] | None, *,
                   closing: bool = False) -> list[dict]:
    """`sections` with no 「[n]」 in the text and the sources it cited (and the attached
    files) named at the end: a 「근거 자료」 section, or a 「※ 근거 자료:」 line on the last
    section of a document that closes with 「끝.」 (공문), where no section follows the body."""
    by_number = {int(s.get("ordinal") or i): s for i, s in enumerate(sources or [], 1)}
    order = [n for n in citations.cited(sections) if n in by_number]
    order += [n for n, s in by_number.items() if s.get("attached") and n not in order]
    labels = list(dict.fromkeys(label for n in order if (label := _label(by_number[n]))))
    out = without_citations(sections)
    if not labels:
        return out
    if closing and out:
        last = out[-1]
        out[-1] = {**last, "content": (str(last.get("content") or "").rstrip()
                                       + f"\n\n※ {EVIDENCE_HEADING}: " + ", ".join(labels))}
        return out
    return [*out, {"id": "evidence", "heading": EVIDENCE_HEADING, "level": 1, "status": "done",
                   "content": "\n".join(f"- {label}" for label in labels)}]


# ── 「(확인 필요)」 → a named blank ──

_MARK = r"\s?[(\[（]\s?확인\s?필요\s?[)\]）]"
_UNIT_NAMES = (
    (r"%|％|퍼센트|%p|%포인트", "비율"), (r"배", "배수"), (r"(?:천|만|억|조)?\s?원", "금액"),
    (r"명", "인원"), (r"건", "건수"), (r"년", "연도"), (r"(?:개)?월", "월"),
    (r"시간|분|초|ms|밀리초|s(?![A-Za-z])", "시간"), (r"(?:GB|MB|TB|KB)", "용량"),
    (r"(?:tokens?|토큰)(?:/s)?", "토큰 수"), (r"개", "개수"), (r"회", "횟수"), (r"점", "점수"),
)
_UNIT = "|".join(f"(?:{u})" for u, _ in _UNIT_NAMES)
_VALUE_MARK = re.compile(
    r"(?P<value>(?:약\s?|최대\s?|최소\s?|평균\s?)?[-+]?\d[\d,.]*(?:\s?[~–-]\s?\d[\d,.]*)?"
    rf"\s?(?P<unit>{_UNIT})?)" + _MARK)
#: the field a value is given for, just before it: 「처리량은 」, 「TTFT가 」, 「예산:」
_FIELD_BEFORE = re.compile(
    r"(?P<field>[가-힣A-Za-z][가-힣A-Za-z0-9·-]{0,14}?)(?:은|는|이|가|을|를|도|만)?\s{0,2}[:：]?"
    r"\s{0,2}$")
_PARTICLE_END = re.compile(r"(?:은|는|이|가|을|를|도)\s{0,2}$|[:：]\s{0,2}$")
_SENTENCE = re.compile(r"[^.!?。\n]*(?:[.!?。](?=\s|$)|$)")


def _unit_name(unit: str) -> str:
    for pattern, name in _UNIT_NAMES:
        if re.fullmatch(pattern, unit.strip()):
            return name
    return ""


def _value_blank(before: str, unit: str) -> str:
    """The blank a marked value becomes: the field written just before it (「처리량은」),
    else what its unit measures (「%」 → 비율); "" when neither names it."""
    name = _unit_name(unit or "")
    if name in ("연도", "월"):
        # a year is its own field: 「이 방식은 2023년」 is no 방식
        return f"[{name}]"
    if _PARTICLE_END.search(before) and (m := _FIELD_BEFORE.search(before)):
        return f"[{m.group('field')}]"
    return f"[{name}]" if name else ""


def _settle_prose(line: str) -> str:
    """A prose line with each marked value as a named blank and each marked claim that is no
    value (or names no field) dropped with its sentence."""
    def value(m: re.Match[str]) -> str:
        blank = _value_blank(line[:m.start()], m.group("unit") or "")
        return blank or m.group(0)

    line = _VALUE_MARK.sub(value, line)
    if not re.search(_MARK, line):
        return line
    lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)])\s{1,4})?", line).group(0)
    sentences = [s for s in _SENTENCE.findall(line[len(lead):]) if s.strip()]
    kept = [s.strip() for s in sentences if not re.search(_MARK, s)]
    return lead + " ".join(kept) if kept else ""


def _settle_table(lines: list[str]) -> list[str]:
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    out = lines[:2]
    for row in lines[2:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        for k, cell in enumerate(cells):
            if not re.search(_MARK, cell):
                continue
            name = header[k] if k < len(header) and header[k] else (cells[0] if cells else "")
            blank = f"[{name}]" if name else ""
            cell = _VALUE_MARK.sub(lambda m, b=blank: b or m.group(0), cell)
            # a marked word in a cell: the cell is the blank for its column
            cells[k] = (blank or re.sub(_MARK, "", cell).strip()) if re.search(_MARK, cell) \
                else cell
        out.append("| " + " | ".join(cells) + " |")
    return out


def settle_marks(text: str) -> str:
    """`text` without 「(확인 필요)」: a marked value becomes the blank naming its field
    (「처리량은 2.3배(확인 필요)」 → 「처리량은 [처리량]」, a table cell its column's
    「[기한]」); a marked claim that is no value goes with its sentence. Code is untouched."""
    if not text or not re.search(_MARK, text):
        return text
    lines = text.split("\n")
    out: list[str] = []
    fenced = False
    k = 0
    while k < len(lines):
        line = lines[k]
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced or line.lstrip().startswith(("```", "~~~", "#", "![")):
            out.append(line)
            k += 1
            continue
        if line.lstrip().startswith("|") and k + 1 < len(lines) and re.match(
                r"^\s*\|?\s*:?-{2,}", lines[k + 1]):
            j = k + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.extend(_settle_table(lines[k:j]))
            k = j
            continue
        settled = _settle_prose(line) if re.search(_MARK, line) else line
        if settled or not line.strip():
            out.append(settled)
        k += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def for_file(data: dict[str, Any], *, format_id: str | None, hwp: bool,
             scholarly: bool) -> dict[str, Any]:
    """The report's `Artifact.data` as its office file carries it: marks settled, and in a
    work document the sources listed instead of numbered (`sourceList`: the file builds no
    reference list of its own)."""
    sections = [{**s, "content": settle_marks(str(s.get("content") or ""))}
                if str(s.get("format") or "markdown") == "markdown" else s
                for s in data.get("sections") or []]
    out = {**data, "sections": sections}
    if plain_sources(format_id, hwp=hwp, scholarly=scholarly):
        closing = bool((data.get("titleBlock") or {}).get("closing"))
        out["sections"] = as_source_list(sections, data.get("sources"), closing=closing)
        out["sourceList"] = True
    return out


__all__ = ["ACADEMIC_FORMATS", "EVIDENCE_HEADING", "WORK_FORMATS", "as_source_list",
           "for_file", "plain_sources", "settle_marks", "without_citations"]
