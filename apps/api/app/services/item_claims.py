"""What a document says of a named item, held to what the material says of that item.

When the material lists compared options one per line, a writer fills every comparison cell and
takes what it lacks from a neighbouring item (「C | 국내 데이터센터」 from B). `misbound` finds
cells
and single-item sentences whose words the material says only of other items; `settled` writes
「자료 없음」 in a cell still standing after repair. `owner_blanks` finds empty owner cells
(「담당 | [담당자]」) of a comparison or plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: an item line of the material: its enumerator or name, a colon, what is said of it
_ITEM_LINE = re.compile(
    r"^\s{0,6}(?:[-*•·]\s{0,3})?(?P<head>(?:[A-Za-z가-힣]|\d{1,2}|[①-⑩])[.)]\s{0,3}[^:：\n]{1,60}?"
    r"|[^:：\n]{0,40}?['‘\"“「][^'’\"”」\n]{1,30}['’\"”」][^:：\n]{0,30}?)\s{0,2}[:：]\s{0,3}"
    r"(?P<body>[^\n]{4,})$", re.M)
#: names inside a head: what it quotes
_QUOTED = re.compile(r"['‘\"“「]([^'’\"”」\n]{1,30})['’\"”」]")
#: the enumerator a head starts with: 「A.」 「B)」
_LETTER = re.compile(r"^\s{0,6}(?:[-*•·]\s{0,3})?([A-Za-z]|\d{1,2})[.)]")
_WORD = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9]*")
#: particles and endings a word is compared without
_PARTICLE = re.compile(
    r"(?:으로|에서|에게|까지|부터|이며|이고|이나|하고|하며|와|과|은|는|이|가|을|를|의|에|로|도|만)$")
#: a cell or sentence that already says the material lacks it
_ADMITS = re.compile(r"자료\s?없음|자료에\s?없|명시\s?없|명시되지|확인(?:이|을)?\s?(?:필요|해야)"
                     r"|자료에[^|.\n]{0,24}없|정보가?\s?없|미확인|불명확|확인되지|미정|알\s?수\s?없|-$|^-")
#: words too general to tie a phrase to one item
_GENERAL = frozenset({"기능", "지원", "제공", "가능", "비용", "연간", "월", "원", "기준", "사용",
                      "도입", "수준", "있음", "없음", "있다", "없다", "보통", "높음", "낮음"})


@dataclass(frozen=True, slots=True)
class Item:
    names: frozenset[str]
    letter: str
    body: str


def _stem(word: str) -> str:
    stem = _PARTICLE.sub("", word)
    return stem if len(stem) >= 2 or not stem.isalpha() else word


def _pairs(text: str) -> set[tuple[str, str]]:
    """Neighbouring word pairs of `text` (no figures), particles off: 「국내 데이터센터」."""
    words = [_stem(w) for w in _WORD.findall(text or "")]
    return {(a, b) for a, b in zip(words, words[1:], strict=False)
            if not (a in _GENERAL and b in _GENERAL) and not any(c.isdigit() for c in a + b)}


def items_of(material: str) -> list[Item]:
    """The material's item lines, two or more of them, each with its names and what it says."""
    out = []
    for m in _ITEM_LINE.finditer(material or ""):
        head = m.group("head")
        names = {n.strip().lower() for n in _QUOTED.findall(head) if n.strip()}
        letter = (_LETTER.match(head).group(1) if _LETTER.match(head) else "").upper()
        if not names and not letter:
            continue
        out.append(Item(frozenset(names), letter, m.group("body")))
    return out if len(out) >= 2 else []


def _outside(material: str) -> str:
    """The material's words that are not any item's."""
    return _ITEM_LINE.sub("", material or "")


def _named(text: str, item: Item) -> bool:
    """Whether `text` (a column title, a sentence) names `item`."""
    low = (text or "").lower()
    if any(name in low for name in item.names):
        return True
    return bool(item.letter) and bool(re.match(
        rf"^\s*\**\s*{re.escape(item.letter)}(?:안|사|\.|\)|\s|$)", text or "", re.I))


def foreign(text: str, item: Item, items: list[Item], outside: str) -> tuple[str, str]:
    """A phrase of `text` the material says only of items other than `item`, with the items it
    says it of; ('', '') when none."""
    mine, rest = _pairs(item.body), _pairs(outside)
    for pair in sorted(_pairs(text)):
        if pair in mine or pair in rest:
            continue
        owners = [o for o in items if o is not item and pair in _pairs(o.body)]
        if owners:
            return " ".join(pair), ", ".join(_item_label(o) for o in owners)
    return "", ""


def _terms(text: str) -> set[str]:
    """The content words of a row label (「한국어 지원」 → 한국어), particles off."""
    return {t for t in (_stem(w) for w in _WORD.findall(text or ""))
            if len(t) >= 2 and t not in _GENERAL and not any(c.isdigit() for c in t)}


def unsaid(label: str, cell: str, item: Item, items: list[Item], outside: str) -> tuple[str, str]:
    """A row label's attribute the material gives only for other items, answered in `item`'s cell
    with something the material never says of it; ('', '') when the cell holds `item`'s own
    words, the label's words are said of it, or they are in the material at large."""
    terms = {t for t in _terms(label) if t not in outside}
    if not terms or any(t in item.body for t in _terms(label)):
        return "", ""
    if _pairs(cell) & _pairs(item.body) or (cell.strip() and cell.strip() in item.body):
        return "", ""
    for term in sorted(terms):
        owners = [o for o in items if o is not item and term in o.body]
        if owners:
            return term, ", ".join(_item_label(o) for o in owners)
    return "", ""


@dataclass(frozen=True, slots=True)
class Misbound:
    section: int
    line: str
    item: str
    phrase: str
    owners: str
    #: the table cell's column, or -1 for a sentence
    column: int = -1


def _item_label(item: Item) -> str:
    return next(iter(sorted(item.names)), "") or item.letter


def _rows(content: str):
    """Markdown tables of `content` as (header cells, [(line, cells)])."""
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            block = [line for line in lines[i:j] if not re.fullmatch(r"[\s|:-]+", line)]
            if len(block) >= 2:
                cells = [[c.strip() for c in line.strip().strip("|").split("|")] for line in block]
                yield cells[0], list(zip(block[1:], cells[1:], strict=True))
            i = j
            continue
        i += 1


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
#: where a sentence's clauses part: 「…원활하고, …」 「…풍부하지만 …」
_CLAUSE = re.compile(r"[,;]\s|(?<=[가-힣])(?:고|며|지만|으나|는데|면서)\s")


def misbound(sections: list[dict], material: str) -> list[Misbound]:
    """Table cells and one-item sentences saying of a named item what the material says only of
    other items."""
    items = items_of(material)
    if not items:
        return []
    outside = _outside(material)
    out: list[Misbound] = []
    for index, sec in enumerate(sections):
        content = str(sec.get("content") or "")
        for header, rows in _rows(content):
            columns = {}
            for j, title in enumerate(header):
                hits = [it for it in items if _named(title, it)]
                if j and len(hits) == 1:
                    columns[j] = hits[0]
            if len(columns) < 2:
                continue
            for line, cells in rows:
                for j, item in columns.items():
                    if j >= len(cells) or not cells[j] or _ADMITS.search(cells[j]):
                        continue
                    phrase, owners = foreign(cells[j], item, items, outside)
                    if not phrase and cells:
                        phrase, owners = unsaid(cells[0], cells[j], item, items, outside)
                    if phrase:
                        out.append(Misbound(index, line, _item_label(item), phrase, owners, j))
        prose = "\n".join(line for line in content.split("\n")
                          if not line.strip().startswith(("|", "!", "#")))
        for m in _SENTENCE.finditer(prose):
            sentence = m.group(0).strip()
            if _ADMITS.search(sentence) or len(sentence) < 8:
                continue
            for clause in _CLAUSE.split(sentence):
                # what a clause says is of the item it names, and of that item alone
                hits = [it for it in items
                        if it.names and any(n in clause.lower() for n in it.names)]
                if len(hits) != 1:
                    continue
                phrase, owners = foreign(clause, hits[0], items, outside)
                if phrase:
                    out.append(Misbound(index, sentence, _item_label(hits[0]), phrase, owners))
                    break
    return out


def still_misbound(content: str, line: str, new: str, material: str) -> bool:
    """Whether `new`, standing where `line` stood in `content`, still gives an item another
    item's attribute (a table row is read under its own table's header)."""
    if line not in content:
        content = new
    else:
        content = content.replace(line, new, 1)
    return any(f.line == new.strip() or f.line == new
               for f in misbound([{"content": content}], material))


def why(found: Misbound) -> str:
    where = (f"표의 「{found.item}」 칸에 쓴" if found.column >= 0
             else f"「{found.item}」에 대해 쓴")
    return (f"{where} 「{found.phrase}」는 자료가 다른 항목({found.owners})에 대해서만 준 "
            f"내용이다. 자료가 「{found.item}」에 대해 준 내용으로 고치고, 자료에 없으면 그 "
            "칸은 「자료 없음」으로, 문장은 그 내용을 빼고 써라.")


def settled(sections: list[dict], material: str) -> tuple[list[dict], int]:
    """Cells `misbound` still finds, written 「자료 없음」; sentences are left to the report."""
    found = [f for f in misbound(sections, material) if f.column >= 0]
    if not found:
        return sections, 0
    out = [dict(sec) for sec in sections]
    count = 0
    for f in found:
        content = str(out[f.section].get("content") or "")
        if f.line not in content:
            continue
        lead = re.match(r"^\s*", f.line).group(0)
        cells = f.line.strip().strip("|").split("|")
        if f.column >= len(cells):
            continue
        cells[f.column] = " 자료 없음 "
        new = lead + "|" + "|".join(cells) + "|"
        out[f.section]["content"] = content.replace(f.line, new, 1)
        count += 1
    return out, count


#: an owner left as a bracket: 「[담당자]」 「[담당 부서]」 「[책임자]」
OWNER_BLANK = re.compile(
    r"\[(?:담당\s?(?:자|부서|팀|조직)?|책임\s?(?:자|부서)|주관\s?(?:부서|기관)|부서명?)\]")


#: a task's deadline left as a bracket: 「[기한]」 「[완료일]」 「[일정]」
DUE_BLANK = re.compile(r"\[(?:기한|마감\s?일?|완료\s?(?:일|시점)|일정|시점|목표\s?일)\]")


def owner_blanks(sections: list[dict], blank: re.Pattern = OWNER_BLANK) -> list[tuple[int, str]]:
    """Table rows and sentences leaving a task's owner (or, with `DUE_BLANK`, its deadline) as
    a bracket blank."""
    out = []
    for index, sec in enumerate(sections):
        for line in str(sec.get("content") or "").split("\n"):
            if not blank.search(line):
                continue
            if line.strip().startswith("|"):
                out.append((index, line))
                continue
            out += [(index, m.group(0).strip()) for m in _SENTENCE.finditer(line)
                    if blank.search(m.group(0))]
    return out


OWNER_WHY = ("일을 맡을 곳을 「[담당자]」 같은 빈칸으로 두었다. 사람 이름은 지어내지 말고, 그 일의 "
             "성격으로 맡을 부서나 역할을 제안해 적어라(예: 「IT 담당 부서」, 「고객센터」, "
             "「물류센터 운영팀」). 자료가 맡을 곳을 정했으면 그대로 쓴다.")

DUE_WHY = ("일의 기한을 「[기한]」 같은 빈칸으로 두었다. 날짜를 지어내지 말고, 자료가 준 일정에 "
           "맞춰 단계로 적거나(예: 「2월 시범 운영 전」, 「4월 전사 적용 시」, 「4분기 중」) "
           "자료에 일정이 없으면 「일정 확정 후」처럼 문장으로 써라.")
