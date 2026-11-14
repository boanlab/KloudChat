"""Requests that ask about every item of the material, and which items an answer left out.

「각 강에 대한 한줄 요약」, 「모든 자료를 정리」, 「주차별로」, 「each lecture」, 「21개 강의」
ask for one entry per file; a turn that answers them from a handful of passages drops the
rest silently. `asks_each` tells such a request; `missing` names the files an answer never
reached, so the answer can say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import file_digest

#: what one file of a course or a collection is called
_UNIT = (r"(?:강의|강|장|주차|회차|차시|단원|챕터|자료|파일|문서|항목|과목|논문|보고서"
         r"|슬라이드|발표|교안|노트)")
#: a particle at most after the unit: 「각 강에」 「모든 자료를」, never 「각 장점」
_TAIL = r"(?:에서|에게|마다|별로|별|에|의|을|를|은|는|이|가|과|와|도|로)?(?![가-힣])"
_ASKS_EACH = re.compile(
    rf"각(?:각의?)?\s{{0,2}}{_UNIT}{_TAIL}"
    rf"|모든\s{{0,2}}{_UNIT}{_TAIL}"
    r"|(?:자료|파일|문서|강의)\s{0,2}(?:전체|전부|모두|하나하나)"
    r"|전체\s{0,2}(?:자료|파일|문서|강의|목록)"
    rf"|(?<![가-힣])매\s{{0,2}}(?:주차|강|회차|차시){_TAIL}"
    r"|(?<![가-힣])(?:자료|파일|문서|강의|강|주차|장|회차|챕터|단원|차시|과목)\s{0,2}별(?:로)?(?![가-힣])"
    r"|하나하나|빠짐\s{0,2}없이|빠뜨리지\s{0,2}말|일일이"
    rf"|\d{{1,3}}\s{{0,2}}개(?:의)?\s{{0,2}}{_UNIT}{_TAIL}"
    r"|\b(?:each|every|all(?:\s+the|\s+of\s+the)?)\s+(?:of\s+the\s+)?"
    r"(?:lectures?|files?|documents?|chapters?|weeks?|sessions?|items?|sources?|papers?|units?|"
    r"modules?|slides?)\b"
    r"|\bper[- ](?:lecture|file|document|chapter|week|session|unit)\b",
    re.I,
)


def asks_each(text: str) -> bool:
    """Whether the request asks for every item of the material, one by one."""
    return bool(_ASKS_EACH.search(text or ""))


@dataclass(frozen=True, slots=True)
class Item:
    """One file an each-item answer should reach, and the words that show it did."""

    name: str
    label: str = ""
    title: str = ""
    #: the file's citation number in a document (「[3]」), 0 = none
    ordinal: int = 0

    def wire(self) -> dict:
        return {"name": self.name, "label": self.label, "title": self.title,
                "ordinal": self.ordinal}

    @classmethod
    def of(cls, raw: dict) -> Item:
        return cls(str(raw.get("name") or ""), str(raw.get("label") or ""),
                   str(raw.get("title") or ""), int(raw.get("ordinal") or 0))

    @property
    def shown(self) -> str:
        """How the note names it: its unit label when it has one, else its file name."""
        return self.label or self.name


def _flat(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (text or "").lower())


def _label_pattern(label: str) -> re.Pattern[str] | None:
    found = re.fullmatch(r"(\d{1,3})(강|주차|차시|회차|장|단원)", label)
    if found:
        number, unit = found.groups()
        return re.compile(rf"(?<![0-9])0*{number}\s{{0,2}}{unit}")
    found = re.fullmatch(r"([A-Za-z]+) (\d{1,3})", label)
    if found:
        unit, number = found.groups()
        return re.compile(rf"\b{unit}[\s._-]{{0,3}}0*{number}(?![0-9])", re.I)
    return None


def reached(item: Item, text: str, flat: str | None = None) -> bool:
    """Whether `text` names the item: its unit label, its title, its file name, or its
    citation number."""
    flat = _flat(text) if flat is None else flat
    if item.label and (pattern := _label_pattern(item.label)) and pattern.search(text):
        return True
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", item.name)
    for words in (item.title, stem):
        key = _flat(words)
        if len(key) >= 4 and key in flat:
            return True
    return bool(item.ordinal and re.search(rf"\[{item.ordinal}\](?!\()", text))


def missing(items: list[Item], text: str) -> list[Item]:
    """Items `text` never names, in their order."""
    flat = _flat(text)
    return [item for item in items if item.name and not reached(item, text, flat)]


#: 「1~3강」 「13-16주차」: a heading that groups a range of units
_RANGE = re.compile(r"(?<![0-9])(\d{1,3})\s{0,2}(?:강|주차|차시|회차|장|단원)?\s{0,2}"
                    r"[~∼〜\-–]\s{0,2}(\d{1,3})\s{0,2}(강|주차|차시|회차|장|단원)")


def _in_range(item: Item, heading: str) -> bool:
    found = re.fullmatch(r"(\d{1,3})(강|주차|차시|회차|장|단원)", item.label)
    if not found:
        return False
    number, unit = int(found.group(1)), found.group(2)
    return any(m.group(3) == unit and int(m.group(1)) <= number <= int(m.group(2))
               for m in _RANGE.finditer(heading))


def outline_gaps(items: list[Item], headings: list[str]) -> list[Item]:
    """Items no 목차 heading reaches — by name, title or label, or inside a heading's range
    of units (「1~3강 …」 reaches 2강)."""
    joined = "\n".join(headings)
    flat = _flat(joined)
    return [item for item in items if item.name and not reached(item, joined, flat)
            and not any(_in_range(item, heading) for heading in headings)]


def note(left_out: list[Item], total: int) -> str:
    """The line an answer ends with when it reached fewer files than it was asked about."""
    names = ", ".join(item.shown for item in left_out)
    return (f"※ 자료 {total}건 중 {len(left_out)}건은 이 답에서 다루지 못했습니다: {names}. "
            "필요하면 그 자료를 지정해 다시 요청해 주세요.")


def items_of(files: list) -> list[Item]:
    """Coverage items of stored files, labelled from their digests."""
    out = []
    for stored in files:
        digest = file_digest.digest_for(stored)
        out.append(Item(stored.name, digest.label, digest.title))
    return out


__all__ = ["Item", "asks_each", "items_of", "missing", "note", "outline_gaps", "reached"]
