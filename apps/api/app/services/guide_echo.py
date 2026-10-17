"""Guide echoes: what the writer copied from its own guidance into the answer.

The guidance is what the turn's skills and template told the writer (each skill's body, a
template's instructions, genre and checklist) — the same for a shipped skill and one a person
wrote. Two things from it may not reach the reader unless the person's input or sources also
give them:

- example values: a person's name the guidance uses as an example (「김현수 드림」 in an
  output format) becomes 「[이름]」; a full date from the guidance's examples
  (「2026. 12. 31.」) becomes 「[날짜]」;
- rule lines: a sentence that repeats a stretch of the guidance's own wording (outside its
  quoted 「…」 examples, tables and fenced formats, which are there to be copied) and reads as a
  rule for the writer (「자료에 없는 규모·비율은 지어내지 않습니다」) is dropped.

Deterministic, every model group: neither is ever right in an answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: common Korean surnames (one syllable)
_SURNAMES = ("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진"
             "나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경")
#: what follows a name: a sign-off, a self-introduction, an honorific or a title
_AFTER = (r"드림|올림|배상|입니다|이며|이고|님|씨|과장|대리|팀장|차장|부장|주임|사원"
          r"|매니저|책임|선임|주무관|교수|대표|이사|실장|본부장")
#: what comes before one: 「담당: 김현수」, 「작성자 김현수」
_BEFORE = r"(?:담당자?|발신인?|작성자|기안자|보내는\s?사람|저는)\s?[:：]?\s?"
_NAME = rf"[{_SURNAMES}][가-힣]{{2}}"
#: a name in the guidance: 「김현수 드림」, 「담당: 김현수」
_NAME_IN_GUIDE = re.compile(
    rf"(?<![가-힣])({_NAME})(?=\s?(?:{_AFTER})(?![가-힣]))|{_BEFORE}({_NAME})(?![가-힣])")


def _name_in_answer(name: str) -> re.Pattern[str]:
    """`name` where a name stands: before a sign-off or title, after 「담당:」, or alone at a
    line's end (a signature)."""
    n = re.escape(name)
    return re.compile(rf"(?<![가-힣]){n}(?=\s?(?:{_AFTER})(?![가-힣])|[ \t]*$|\s?[|·,])"
                      rf"|(?<=[:：] ){n}(?![가-힣])", re.M)


#: a full date: 「2026. 12. 31.」 「2026-12-31」 「2026년 12월 31일」
_FULL_DATE = re.compile(
    r"(?<![\d.])(20\d{2})\s{0,2}(?:년|[.\-/])\s{0,2}(\d{1,2})\s{0,2}(?:월|[.\-/])\s{0,2}(\d{1,2})"
    r"(?!\d)(?:\s{0,2}일|\.)?")
#: …as written, its weekday with it: 「2026. 10. 20.(화)」
_DATE_WRITTEN = re.compile(_FULL_DATE.pattern + r"(?:\s{0,2}\(\s?[월화수목금토일](?:요일)?\s?\))?")
#: month and day in the person's words: 「12월 31일」 「12/31」 「12. 31.」
_MONTH_DAY = re.compile(
    r"(?<![\d.])(\d{1,2})\s{0,2}월\s{0,2}(\d{1,2})\s{0,2}일|(?<![\d./])(\d{1,2})\s{0,1}(?:/|\.\s?)"
    r"(\d{1,2})(?![\d/])")
#: parts of the guidance meant to be copied as they stand
_COPYABLE = re.compile(r"```[\s\S]*?```|「[^」\n]*」|『[^』\n]*』|`[^`\n]*`|\"[^\"\n]*\"")
_FENCE = re.compile(r"```[\s\S]*?```")
#: a sentence about how the text is written: a rule for the writer, not for the reader
_RULE = re.compile(
    r"지어내지|짓지\s?않|쓰지\s?않|적지\s?않|넣지\s?않|만들지\s?않|붙이지\s?않|비웁니다|비워\s?둡|빈칸"
    r"|대괄호|자료에\s?없는|요청에\s?없는|자료가\s?(?:없|주지)|스크립트|확인\s?필요로|표시합니다"
    r"|따릅니다|따른다|금지|(?:적용|기재|표기|명시)(?:한다|합니다|할\s?것)?\s?[.。]?$")
#: a stretch of the guidance's own words (spaces ignored) long enough to be no coincidence
_RUN = 16
_SENTENCE = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)|(?<=요\.)")
#: placeholders
NAME_BLANK = "[이름]"
DATE_BLANK = "[날짜]"


@dataclass(slots=True)
class Echoes:
    text: str
    names: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.names or self.dates or self.rules)

    def note(self) -> str:
        parts = []
        if self.names:
            parts.append("예시 이름 → [이름]: " + ", ".join(dict.fromkeys(self.names)))
        if self.dates:
            parts.append("예시 날짜 → [날짜]: " + ", ".join(dict.fromkeys(self.dates)))
        if self.rules:
            parts.append(f"작성 규칙 문장 뺌 {len(self.rules)}개")
        return " · ".join(parts)


def guidance(skill_keys: Iterable[str] = (), specs: Mapping[str, Any] | None = None,
             template: Any = None) -> str:
    """The guidance text the writer read: each skill's body (shipped or a person's own, by its
    turn key) and a template's instructions, genre and checklist."""
    from app.services import skill_specs

    parts = []
    for key in dict.fromkeys(skill_keys or ()):
        spec = skill_specs.resolve(key, specs)
        if spec is not None and spec.body:
            parts.append(spec.body)
    if template is not None:
        parts += [str(getattr(template, name, "") or "")
                  for name in ("instructions", "genre", "checklist")]
    return "\n\n".join(p for p in parts if p.strip())


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _names(guide: str) -> set[str]:
    return {a or b for a, b in _NAME_IN_GUIDE.findall(guide)}


def _days(text: str) -> set[tuple[int | None, int, int]]:
    """Days `text` names, with and without the year."""
    out: set[tuple[int | None, int, int]] = set()
    for y, m, d in _FULL_DATE.findall(text or ""):
        out |= {(int(y), int(m), int(d)), (None, int(m), int(d))}
    for m1, d1, m2, d2 in _MONTH_DAY.findall(text or ""):
        out.add((None, int(m1 or m2), int(d1 or d2)))
    return out


def _runs(guide: str) -> set[str]:
    """Every `_RUN`-character stretch of the guidance's own (not copyable) words."""
    own = _COPYABLE.sub("\n", guide)
    out: set[str] = set()
    for line in own.split("\n"):
        if line.lstrip().startswith(("|", "#")):
            continue
        flat = _flat(line)
        if len(re.findall(r"[가-힣]", flat)) < _RUN // 2:
            continue
        out |= {flat[i:i + _RUN] for i in range(len(flat) - _RUN + 1)}
    return out


def _echoes_rule(sentence: str, runs: set[str], evidence_flat: str) -> bool:
    flat = _flat(sentence)
    if len(flat) < _RUN or not _RULE.search(sentence.strip()):
        return False
    for i in range(len(flat) - _RUN + 1):
        run = flat[i:i + _RUN]
        if run in runs and run not in evidence_flat and len(re.findall(r"[가-힣]", run)) >= 8:
            return True
    return False


def scrub(answer: str, guide: str, evidence: Iterable[str]) -> Echoes:
    """`answer` with the guidance's example names and dates blanked and its rule lines dropped,
    where the evidence (the person's input, sources, tool output) does not give them. Fenced
    code stays; the answer is never emptied."""
    out = Echoes(answer)
    if not answer or not guide or not guide.strip():
        return out
    seen = "\n".join(e for e in evidence if e)
    seen_flat = _flat(seen)
    fences = [m.span() for m in _FENCE.finditer(answer)]

    def fenced(at: int) -> bool:
        return any(a <= at < b for a, b in fences)

    text = answer
    # example names, in a name's place in the answer
    for name in sorted(n for n in _names(guide) if n not in seen):
        text, n = _name_in_answer(name).subn(
            lambda m: m.group(0) if fenced(m.start()) else NAME_BLANK, text)
        if n:
            out.names.append(name)
    # example dates
    given = _days(seen)
    examples = {(int(y), int(m), int(d)) for y, m, d in _FULL_DATE.findall(guide)}
    if examples:
        def date(m: re.Match) -> str:
            day = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if day not in examples or day in given or (None, day[1], day[2]) in given \
                    or fenced(m.start()):
                return m.group(0)
            out.dates.append(m.group(0).strip())
            return DATE_BLANK
        text = _DATE_WRITTEN.sub(date, text)
    # rule lines
    runs = _runs(guide)
    if runs:
        lines = text.split("\n")
        kept: list[str] = []
        fence = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("```"):
                fence = not fence
            if fence or stripped.startswith(("```", "#")) or re.search(r"<[a-z/]", line):
                # a fence, a heading or HTML markup: kept whole
                kept.append(line)
                continue
            if stripped.startswith("|"):
                cells = line.split("|")
                for j, cell in enumerate(cells):
                    if _echoes_rule(cell, runs, seen_flat):
                        out.rules.append(cell.strip())
                        cells[j] = " "
                kept.append("|".join(cells))
                continue
            lead = re.match(r"\s{0,24}(?:(?:[-*+]|\d{1,3}[.)]|[※□■○◦ㆍ·])\s{1,4})?", line).group(0)
            sentences = [s for s in _SENTENCE.split(line[len(lead):]) if s.strip()]
            left = [s for s in sentences if not _echoes_rule(s, runs, seen_flat)]
            if len(left) == len(sentences):
                kept.append(line)
                continue
            out.rules += [s.strip() for s in sentences if s not in left]
            if left:
                kept.append(lead + " ".join(s.strip() for s in left))
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
        text = cleaned if cleaned.strip() else text
    out.text = text
    return out


__all__ = ["DATE_BLANK", "NAME_BLANK", "Echoes", "guidance", "scrub"]
