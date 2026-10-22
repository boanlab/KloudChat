"""What a meeting memo says of the meeting itself, read by code, and minutes held to it.

A memo opens with the meeting (「10/14 교육혁신팀 주간회의. 참석 …」) and may close with the
next one (「다음 회의 10/21 화 10시 3층 회의실.」). Minutes that call a day, clock, place or
attendance missing (「시간은 자료에 없음」, 「장소 | 미정」, 「나와 있는 내용은 날짜뿐」) while the
memo gives it get the memo's value or lose the claim; a head field left empty takes the memo's
value or a bracket to fill; and every decision the memo states (「… 50만원으로 줄이자고 했고
합의」) gets a row of the decision table.
"""

from __future__ import annotations

import dataclasses
import re

from app.services.doc_formats import _NEXT_MEETING
from app.services.stated_days import _words

#: a day: 「2026년 10월 21일(수)」, 「2026-10-21」, 「10/14」, 「10월 14일」
_DAY = re.compile(
    r"(?:20\d{2}\s{0,2}년\s{0,2})?\d{1,2}\s{0,2}월\s{0,2}\d{1,2}\s{0,2}일"
    r"|20\d{2}\s{0,2}[-./]\s{0,2}\d{1,2}\s{0,2}[-./]\s{0,2}\d{1,2}"
    r"|(?<![\d/])\d{1,2}\s{0,2}/\s{0,2}\d{1,2}(?![\d/])")
_WEEKDAY = re.compile(
    r"\s{0,2}(?:\(\s{0,2}[월화수목금토일](?:요일)?\s{0,2}\)|(?<![가-힣])[월화수목금토일](?:요일)?(?![가-힣]))")
#: a clock: 「10시」, 「오후 2시 30분」, 「14:00」 (not 「1시간」)
_CLOCK = re.compile(
    r"(?:(?:오전|오후)\s{0,2})?\d{1,2}\s{0,2}시(?![간작각])(?:\s{0,2}\d{1,2}\s{0,2}분|\s{0,2}반)?"
    r"|(?<![\d:])\d{1,2}:\d{2}(?![\d:])")
#: a place said with its label: 「장소 본사 5층 대회의실」, 「장소: 3층」
_PLACE_SAID = re.compile(r"장소\s{0,2}[:：]?\s{0,2}(?:는|은)?\s{0,2}([^,.;\n()]{2,30})")
#: a place by its kind: 「3층 회의실」, 「본관 302호」, 「온라인」
_PLACE = re.compile(
    r"(?:[가-힣A-Za-z0-9]{1,12} ){0,2}[가-힣A-Za-z0-9]{0,12}"
    r"(?:회의실|강의실|세미나실|강당|교실|라운지|사무실|접견실|\d{2,4}\s{0,2}호(?![가-힣]))"
    r"|온라인|화상\s{0,2}회의|비대면|Zoom|줌|Teams|팀즈")
_ATTENDED = re.compile(r"참석(?:자|인원)?\s{0,2}[:：]?\s{0,2}([^.\n;]{2,80})")
_MEETING_WORD = re.compile(r"회의|위원회|미팅|모임|세미나|워크숍|간담회|협의회")
#: where the memo turns from the meeting's head to its agenda
_AGENDA_START = re.compile(r"안건|의제|논의|결정|^\s{0,2}\d{1,2}[.)]")


@dataclasses.dataclass
class Meeting:
    """One meeting as the memo gives it; "" for what it does not give."""

    name: str = ""
    day: str = ""
    clock: str = ""
    place: str = ""
    attendees: str = ""

    def has(self, field: str) -> bool:
        if field == "일시":
            return bool(self.day or self.clock)
        return bool(self.value(field))

    def value(self, field: str) -> str:
        return {"시간": self.clock, "날짜": self.day, "장소": self.place,
                "참석": self.attendees, "일시": " ".join(filter(None, [self.day, self.clock])),
                }.get(field, "")


@dataclasses.dataclass
class Memo:
    this: Meeting
    next: Meeting
    #: the memo less its next-meeting clauses
    rest: str = ""


#: the block of card answers and server rules the request carries after the memo
_CONDITIONS = "\n\n덧붙인 조건:"
#: a rule line of that block, in the server's 한다체 (「…지어내지 않는다: …」, 「…쓴다(…).」)
_RULE = re.compile(r"(?:는|[한쓴둔힌진킨긴])다(?=[\s.(:,)」]|$)")


def of_request(request: str) -> str:
    """The meeting as a request gives it: the memo before the 「덧붙인 조건」 block and the
    block's answer lines (「회의 일시: 10월 7일」), none of its rule lines."""
    memo, _, block = (request or "").partition(_CONDITIONS)
    answers = [ln.strip()[1:].strip() for ln in block.split("\n")
               if ln.strip().startswith("-") and not _RULE.search(ln)]
    return "\n".join([memo.strip(), *answers]).strip()


def _stretches(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<!\d)\.(?!\d)|[\n;。]", text or "") if p.strip()]


def _place(text: str) -> str:
    # dates and clocks out first: 「10/21 화 10시 3층 회의실」 is a room, not 「10시 3층 회의실」
    bare = _CLOCK.sub(",", _WEEKDAY.sub(",", _DAY.sub(",", text)))
    if m := _PLACE_SAID.search(bare):
        value = m.group(1).strip()
        if value and not re.fullmatch(r"(?:미정|없음|-)", value):
            return value
    if m := _PLACE.search(bare):
        return m.group(0).strip()
    return ""


def _meeting(stretches: list[str], *, named: bool) -> Meeting:
    text = ", ".join(stretches)
    found = Meeting()
    if m := _DAY.search(text):
        weekday = _WEEKDAY.match(text, m.end())
        found.day = (m.group(0) + (weekday.group(0) if weekday else "")).strip()
    if m := _CLOCK.search(text):
        found.clock = m.group(0).strip()
    found.place = _place(text)
    if m := next((m for s in stretches if (m := _ATTENDED.search(s))), None):
        found.attendees = re.split(r"[,，]?\s{0,2}불참", m.group(1))[0].strip(" ,")
    if named:
        for stretch in stretches:
            if not _MEETING_WORD.search(stretch) or _ATTENDED.match(stretch):
                continue
            bare = _CLOCK.sub(" ", _WEEKDAY.sub(" ", _DAY.sub(" ", stretch)))
            head = re.split(r"[,，]|\s장소", bare)[0]
            name = " ".join(head.split())
            if _MEETING_WORD.search(name) and len(name) <= 40:
                found.name = name.strip(" ,")
            break
    return found


def read(memo: str) -> Memo:
    """The meeting the memo opens with (its head stretches before the agenda, at most three)
    and the next meeting its own clause gives."""
    clauses = [m.group(0) for m in _NEXT_MEETING.finditer(memo or "")]
    rest = _NEXT_MEETING.sub(" ", memo or "")
    head: list[str] = []
    for stretch in _stretches(rest):
        if _AGENDA_START.search(stretch) or len(head) >= 3:
            break
        head.append(stretch)
    nxt = next((m for c in clauses if (m := _meeting([c], named=False)).has("일시")
                or m.place), Meeting())
    return Memo(_meeting(head, named=True), nxt, rest)


# ── absence claims ──

#: a part of a meeting a sentence may call missing
_FIELDS = (("일시", re.compile(r"일시")), ("시간", re.compile(r"시간(?!대)|시각")),
           ("장소", re.compile(r"장소")), ("날짜", re.compile(r"날짜|일자")),
           ("참석", re.compile(r"참석(?:자|인원)?")))
#: …called missing: 「자료에 나오지 않아」, 「자료에 없음」, 「미정」, 「알 수 없」
_ABSENT = re.compile(
    r"(?:자료|메모|원문|기록)(?:에|에서|에는|상)?\s{0,2}[^\n.]{0,16}?"
    r"(?:나오지|나와\s{0,2}있지|적혀\s{0,2}있지|제시되지|명시되지|기재되지|언급되지|포함되지|없)"
    r"|(?:일시|시간|시각|장소|날짜|일자|참석자?)(?:은|는|이|가)\s{0,2}"
    r"(?:미정|없음|없습니다|없다|정해지지\s{0,2}않|알\s{0,2}수\s{0,2}없|확인되지\s{0,2}않)")
#: …or all but one called missing: 「자료에 나와 있는 내용은 날짜뿐」
_ONLY = re.compile(r"(?:나와\s{0,2}있는|나온|적힌|주어진|있는)\s{0,2}(?:내용|정보)(?:은|는)\s{0,2}"
                   r"([가-힣·, ]{1,16}?)뿐")
_NEXT_SAID = re.compile(r"다음\s{0,2}(?:회의|미팅|모임)|차기")
_NEXT_HEADING = re.compile(r"다음|차기|향후")
#: a cell that gives nothing: 「미정」, 「[장소]」, 「-」, 「확인 필요」
_BLANK_CELL = re.compile(
    r"\**\s{0,2}(?:미정|확인\s{0,2}필요|해당\s{0,2}없음|\[[^\]\n]{1,20}\]|-|—|)\s{0,2}\**")
#: a note in a cell that a part is missing: 「(시간은 자료에 없음)」
_ABSENT_NOTE = re.compile(r"\s{0,2}\(([^()\n]{1,30})\)")
#: a table pointer a dropped sentence may hold: 「(표 5)」
_POINTER = re.compile(r"\(\s{0,2}(?:표|그림)\s{0,2}\d{1,3}\s{0,2}\)")


def _claimed(piece: str) -> set[str]:
    """Parts of a meeting `piece` calls missing."""
    if m := _ONLY.search(piece):
        kept = {name for name, rx in _FIELDS if rx.search(m.group(1))}
        return {"시간", "장소"} - kept - ({"시간"} if "일시" in kept else set())
    if not (m := _ABSENT.search(piece)):
        return set()
    # the parts named up to the claim
    before = piece[:m.end()]
    return {name for name, rx in _FIELDS if rx.search(before)}


def _which(memo: Memo, heading: str, piece: str) -> Meeting:
    return memo.next if _NEXT_HEADING.search(heading or "") or _NEXT_SAID.search(piece) \
        else memo.this


def false_absences(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """Minutes calling a meeting's day, clock, place or attendance missing that the memo gives:
    a blank cell or 「장소: 미정」 line takes the memo's value, a note 「(시간은 자료에 없음)」
    gives way to it, and a sentence claiming so goes (its table pointer kept)."""
    memo = read(memo_text)
    if not any(m.has(f) for m in (memo.this, memo.next) for f in ("일시", "장소", "참석")):
        return sections, 0
    out, fixed = [], 0
    for sec in sections:
        heading = str(sec.get("heading") or "")
        content = str(sec.get("content") or "")
        lines = content.split("\n")
        gone: list[str] = []
        for i, line in enumerate(lines):
            if line.lstrip().startswith("|"):
                lines[i], n = _row(line, memo, heading)
            else:
                lines[i], n = _labelled(line, memo, heading)
                if not n:
                    lines[i], n = _notes(line, _which(memo, heading, line))
                gone += [p for p in _sentences(lines[i]) if _false(p, memo, heading)]
            fixed += n
        text = "\n".join(lines)
        for piece in gone:
            text = drop(text, piece)
        fixed += len(gone)
        out.append({**sec, "content": text} if text != content else sec)
    return out, fixed


def _sentences(line: str) -> list[str]:
    from app.services import units

    if line.lstrip().startswith(("|", "#", "```")):
        return []
    return [p.strip() for p in units.SENTENCE_BREAK.split(line) if p.strip()]


def _false(piece: str, memo: Memo, heading: str) -> bool:
    claim = _claimed(piece)
    meeting = _which(memo, heading, piece)
    return bool(claim) and any(meeting.has(f) for f in claim)


def _field_of(label: str) -> str | None:
    label = label.strip(" *").replace(" ", "")
    return next((name for name, rx in _FIELDS if rx.fullmatch(label)
                 or (rx.search(label) and len(label) <= 6)), None)


def _row(line: str, memo: Memo, heading: str) -> tuple[str, int]:
    parts = line.strip().strip("|").split("|")
    if len(parts) != 2:
        return line, 0
    field = _field_of(parts[0])
    meeting = _which(memo, heading, parts[0])
    if field is None or not meeting.has(field):
        return line, 0
    value = parts[1].strip()
    if _BLANK_CELL.fullmatch(value):
        return f"| {parts[0].strip()} | {meeting.value(field)} |", 1
    noted, n = _notes(value, meeting)
    return (f"| {parts[0].strip()} | {noted.strip()} |", 1) if n else (line, 0)


#: a list line giving a part as nothing: 「- 장소: 미정」, 「**시간**: [시간]」
_LABEL_LINE = re.compile(
    r"^(?P<lead>\s{0,4}(?:[-*•]\s{1,3})?(?:\*\*)?(?P<label>[가-힣 ]{2,8}?)(?:\*\*)?"
    r"\s{0,2}[:：]\s{0,2})"
    r"(?P<blank>미정|확인\s{0,2}필요|\[[^\]\n]{1,20}\]|-|—)\s{0,2}$")


def _labelled(line: str, memo: Memo, heading: str) -> tuple[str, int]:
    m = _LABEL_LINE.match(line)
    if not m or (field := _field_of(m.group("label"))) is None:
        return line, 0
    meeting = _which(memo, heading, m.group("label"))
    if not meeting.has(field):
        return line, 0
    return m.group("lead") + meeting.value(field), 1


def _notes(text: str, meeting: Meeting) -> tuple[str, int]:
    """`text` with each note calling a part missing that `meeting` gives replaced by its value."""
    notes = [m for m in _ABSENT_NOTE.finditer(text)
             if (claim := _claimed(m.group(1))) and all(meeting.has(f) for f in claim)]
    for m in reversed(notes):
        add = " ".join(v for f in sorted(_claimed(m.group(1)))
                       if (v := meeting.value(f)) and v not in text)
        text = text[:m.start()] + (f" {add}" if add else "") + text[m.end():]
    return text, len(notes)


def _points_next(rest: str, tag: str) -> bool:
    """Whether the next block after a dropped sentence is the table or figure `tag` names."""
    line = next((ln for ln in rest.split("\n")[1:] if ln.strip()), "")
    number = re.search(r"\d{1,3}", tag)
    return bool(number) and bool(re.match(
        rf"\s{{0,4}}\*{{0,2}}\s{{0,2}}(?:표|그림)\s{{0,2}}{number.group(0)}(?!\d)", line))


def drop(content: str, piece: str) -> str:
    """`content` without the sentence `piece`, a table pointer it held (「(표 5)」) put on the
    sentence before it, else on a sentence of its own when its table comes next."""
    at = content.find(piece)
    if at < 0:
        return content
    pointers = [p for p in _POINTER.findall(piece) if content.count(p) == 1]
    start = content.rfind("\n", 0, at) + 1
    before = content[start:at].rstrip()
    rest = content[at + len(piece):]
    tag = "".join(pointers)
    head = content[:start]
    if tag and not before:
        # the sentence before, on an earlier prose line
        lines = head.rstrip("\n").split("\n") if head.strip() else []
        k = next((k for k in range(len(lines) - 1, -1, -1) if lines[k].strip()), None)
        if k is not None and not lines[k].lstrip().startswith(("|", "#", "**표", "**그림", "```")) \
                and lines[k].rstrip()[-1:] in ".!?。":
            lines[k] = f"{lines[k].rstrip()[:-1]}{tag}{lines[k].rstrip()[-1]}"
            head = "\n".join(lines) + "\n" * (len(head) - len(head.rstrip("\n")))
            tag = ""
        elif _points_next(rest, tag):
            before = f"정리하면 다음과 같습니다{tag}."
            tag = ""
        else:
            tag = ""
    if tag:
        before = f"{before[:-1]}{tag}{before[-1]}" if before[-1:] in ".!?。" else f"{before} {tag}"
    line_rest, nl, after = rest.partition("\n")
    line = re.sub(r" {2,}", " ", f"{before} {line_rest.strip()}").strip()
    text = head + line + nl + after if line else head.rstrip("\n") + ("\n" + after if nl else "")
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


# ── head fields ──

_HEAD = (("회의명", "name"), ("장소", "place"), ("참석자", "attendees"))
_HEAD_BLANK = {"장소": "[장소]", "회의명": "[회의명]", "참석자": "[참석자]"}


def fill_head(block: dict | None, memo_text: str) -> dict | None:
    """A minutes head whose 회의명, 장소 or 참석자 is empty (or holds the next meeting's room or
    clock) takes the meeting's own value from the memo, else a bracket for the person to fill;
    an 일시 with no clock takes the memo's. None when nothing changes."""
    if not block or not (memo_text or "").strip():
        return None
    memo = read(memo_text)
    this = memo.this
    fields = [list(f) for f in block.get("fields") or []]
    changed = False
    for f in fields:
        if len(f) < 2:
            continue
        name, value = str(f[0]), str(f[1] or "").strip()
        attr = next((a for n, a in _HEAD if name == n or name == f"회의 {n}"), None)
        field = {"장소": "장소", "일시": "시간"}.get(name.removeprefix("회의 "))
        if field and value and (left := _borrowed(field, value, memo)) is not None:
            # the next meeting's room or clock is no value of this one's head
            key = name.removeprefix("회의 ")
            f[1] = _HEAD_BLANK.get(key, f"[{key}]") if left == "미정" else left
            value = str(f[1])
            changed = True
        if attr and not value:
            f[1] = getattr(this, attr) or _HEAD_BLANK[name.removeprefix("회의 ")]
            changed = True
        elif name in ("일시", "회의 일시") and value and this.clock \
                and not _CLOCK.search(value) and "[" not in value:
            f[1] = f"{value} {this.clock}"
            changed = True
    return {**block, "fields": fields} if changed else None


def overview_day(sections: list[dict], head: dict | None, memo_text: str) -> list[dict]:
    """An 일시 row outside the next-meeting part that holds the next meeting's day, not the
    head's, takes the head's 일시."""
    day = next((str(f[1]) for f in (head or {}).get("fields") or []
                if len(f) >= 2 and str(f[0]) in ("일시", "회의 일시")), "")
    nxt = read(memo_text).next.day
    if not day or "[" in day or not nxt:
        return sections
    month_day = _month_day(nxt)
    if not month_day or month_day == _month_day(day):
        return sections
    out = []
    for sec in sections:
        if _NEXT_HEADING.search(str(sec.get("heading") or "")):
            out.append(sec)
            continue
        lines = str(sec.get("content") or "").split("\n")
        for i, line in enumerate(lines):
            parts = line.strip().strip("|").split("|")
            if line.lstrip().startswith("|") and len(parts) == 2 \
                    and re.fullmatch(r"\**\s{0,2}(?:회의\s{0,2})?(?:일시|일자|날짜)\s{0,2}\**",
                                     parts[0].strip()) \
                    and _month_day(parts[1]) == month_day:
                lines[i] = f"| {parts[0].strip()} | {day} |"
        out.append({**sec, "content": "\n".join(lines)})
    return out


#: a field label of a 2-cell row or a list line: 「장소」, 「**회의 일시**」
_ROW_FIELD = re.compile(r"\**\s{0,2}(?:회의\s{0,2})?(장소|일시|시간|시각)\s{0,2}\**")
#: a sentence saying where the meeting is held: 「3층 회의실에서 열렸습니다」
_HELD_AT = re.compile(r"장소|열렸|열린|열리|개최|진행|모여|모였")


def _hours(text: str) -> set[int]:
    out = set()
    for m in _CLOCK.finditer(text or ""):
        hour = int(re.search(r"\d{1,2}", m.group(0)).group(0))
        out.add(hour + 12 if m.group(0).startswith("오후") and hour < 12 else hour)
    return out


def _borrowed(field: str, value: str, memo: Memo) -> str | None:
    """`value` of this meeting's `field` less what is the next meeting's (its place, its clock)
    where the memo gives this meeting none: this meeting's own value, else 「미정」; None when
    nothing is borrowed."""
    this, nxt = memo.this, memo.next
    if field == "장소":
        if not nxt.place or this.place or _flat(nxt.place) not in _flat(value) \
                or _flat(nxt.place) in _flat(memo.rest):
            return None
        return "미정"
    if not nxt.clock or this.clock or not (_hours(value) & _hours(nxt.clock)) \
            or _hours(memo.rest) & _hours(nxt.clock):
        return None
    left = " ".join(_CLOCK.sub(" ", value).split()).strip(" ,")
    return left or "미정"


def borrowed_next(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """This meeting's place or clock outside the next-meeting part that is the next meeting's
    (「장소 | 3층 회의실」 when only the next meeting's clause gives a room): the row or list line
    takes 「미정」 (an 일시 keeps its day), and a sentence holding the meeting there goes."""
    memo = read(memo_text)
    if not (memo.next.place and not memo.this.place) \
            and not (memo.next.clock and not memo.this.clock):
        return sections, 0
    out, fixed = [], 0
    for sec in sections:
        if _NEXT_HEADING.search(str(sec.get("heading") or "")):
            out.append(sec)
            continue
        content = str(sec.get("content") or "")
        lines = content.split("\n")
        gone: list[str] = []
        for i, line in enumerate(lines):
            parts = line.strip().strip("|").split("|")
            if line.lstrip().startswith("|") and len(parts) == 2 \
                    and (m := _ROW_FIELD.fullmatch(parts[0].strip())) \
                    and not _NEXT_SAID.search(parts[1]):
                field = "장소" if m.group(1) == "장소" else "시간"
                if (value := _borrowed(field, parts[1], memo)) is not None:
                    lines[i] = f"| {parts[0].strip()} | {value} |"
                    fixed += 1
            elif not line.lstrip().startswith(("|", "#")) and memo.next.place \
                    and not memo.this.place:
                gone += [p for p in _sentences(line) if _flat(memo.next.place) in _flat(p)
                         and _HELD_AT.search(p) and not _NEXT_SAID.search(p)]
        text = "\n".join(lines)
        for piece in gone:
            text = drop(text, piece)
        fixed += len(gone)
        out.append({**sec, "content": text} if text != content else sec)
    return out, fixed


def _month_day(text: str) -> tuple[int, int] | None:
    m = _DAY.search(text or "")
    if not m:
        return None
    nums = [int(n) for n in re.findall(r"\d{1,4}", m.group(0))]
    nums = nums[1:] if nums and nums[0] > 31 else nums
    return (nums[0], nums[1]) if len(nums) >= 2 else None


# ── decisions ──

#: a clause stating a decision: 「… 확정」, 「… 합의」, 「결정함」
_DECIDED = re.compile(r"확정|합의|결정(?:함|했|됨|되었|하였)|채택|의결|가결")
#: …or one left open: 「결정 못 함」, 「보류」
_UNDECIDED = re.compile(r"못|않|보류|미정|미결|추후|재논의")
_FIGURE = re.compile(r"\d[\d,./]{0,12}(?:\s{0,2}[~∼-]\s{0,2}\d[\d,./]{0,12})?"
                     r"(?:\s{0,2}(?:만원|억원|원|명|건|%|개|곳|회))?")
_DECISION_TABLE = re.compile(r"결정|합의|의결")
_ACTION_TABLE = re.compile(r"실행|할\s{0,2}일|조치|후속|담당|기한")
_ORDINAL = re.compile(r"^(?:첫째|둘째|셋째|넷째|다섯째|마지막으로|또한|그리고)\s{0,2},?\s{0,2}")


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def decisions(memo_text: str) -> list[tuple[str, str]]:
    """(agenda label, clause) of each decision the memo states, the next meeting's clause aside:
    「안건2 예산」, 「박민호가 간식비 80만원을 요청했으나 팀장이 50만원으로 줄이자고 했고 합의」."""
    out = []
    for stretch in _stretches(_NEXT_MEETING.sub(" ", memo_text or "")):
        label, _, body = stretch.partition(":") if re.match(r"[^:：]{1,30}[:：]", stretch) \
            else ("", "", stretch)
        for clause in re.split(r",\s{1,3}|，", body):
            clause = clause.strip()
            if _DECIDED.search(clause) and not _UNDECIDED.search(clause):
                out.append((label.strip(), clause))
    return out


def _days(text: str) -> set[tuple[int, int]]:
    return {d for m in _DAY.finditer(text or "") if (d := _month_day(m.group(0)))}


def _covers(row: str, clause: str) -> bool:
    figures = [_flat(f) for f in _FIGURE.findall(clause)]
    if (days := _days(clause)) and days <= _days(row):
        # 「11/3~11/14」 in a row written 「2026년 11월 3일~2026년 11월 14일」
        return True
    if figures:
        return figures[-1] in _flat(row)
    words = {w[:2] for w in _words(clause)} - {"확정", "합의", "결정"}
    return len(words & {w[:2] for w in _words(row)}) >= 2


def decision_rows(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """A decision of the memo no row of the decision table holds gets one: the minutes' own
    sentence on it (its figure, a decision word), else the memo's clause."""
    from app.services import report_checks, units

    stated = decisions(memo_text)
    if not stated:
        return sections, 0
    found = [t for t in report_checks.tables(sections)
             if (_DECISION_TABLE.search(" ".join(t.header))
                 or _DECISION_TABLE.search(str(sections[t.section].get("heading") or "")))
             and not _ACTION_TABLE.search(" ".join(t.header)) and t.rows]
    if len(found) != 1:
        return sections, 0
    table = found[0]
    prose = [p.strip() for sec in sections for line in str(sec.get("content") or "").split("\n")
             if not line.lstrip().startswith(("|", "#", "**표", "**그림"))
             for p in units.SENTENCE_BREAK.split(line) if p.strip()]
    out = [dict(s) for s in sections]
    added = 0
    rows = [line for line, _ in table.rows]
    for label, clause in stated:
        if any(_covers(r, clause) for r in rows):
            continue
        figures = [_flat(f) for f in _FIGURE.findall(clause)]
        own = next((p for p in prose if _DECIDED.search(p) and figures
                    and figures[-1] in _flat(p) and len(p) <= 120), "")
        text = _POINTER.sub("", _ORDINAL.sub("", own)).strip().rstrip(".") or clause
        row = _decision_row(table, len(rows) + 1, label, text)
        content = str(out[table.section].get("content") or "")
        out[table.section]["content"] = content.replace(rows[-1], f"{rows[-1]}\n{row}", 1)
        rows.append(row)
        added += 1
    return out, added


def _decision_row(table, number: int, label: str, text: str) -> str:
    agenda = re.match(r"안건\s{0,2}\d{1,2}", label)
    cells = []
    for k, head in enumerate(table.header):
        column = [r[k].strip() for _, r in table.rows if len(r) > k]
        if re.search(r"번호|순번|No|#", head) and all(c.isdigit() for c in column):
            cells.append(str(number))
        elif re.search(r"결정|내용|사항", head):
            cells.append(text)
        elif agenda and column and all(re.fullmatch(r"안건\s{0,2}\d{1,2}", c) for c in column):
            cells.append(agenda.group(0))
        elif re.search(r"안건|구분|항목", head) and label:
            cells.append(re.sub(r"^안건\s{0,2}\d{1,2}\s{0,2}", "", label) or label)
        else:
            cells.append("-")
    if text not in cells:
        cells[min(1, len(cells) - 1)] = text
    return "| " + " | ".join(cells) + " |"


#: a value given as what was decided: 「80만원으로 합의했습니다」, 「50만원으로 확정」
_DECIDED_AS = re.compile(
    r"\s?(?:으로|로)\s?(?:[가-힣]{1,6}\s){0,2}(?:합의|확정|결정|정했|정하였|채택|의결|가결)")


def decided_values(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """A sentence or decision row giving the value a memo decision changed (「80만원을 요청했으나
    50만원으로 줄이자고 했고 합의」) as the one decided: 「간식비를 80만원으로 합의했습니다」 takes
    50만원, a decision row holding 80만원 and not 50만원 too."""
    from app.services import key_figures, report_checks, units

    changed = [(clause, c) for _, clause in decisions(memo_text)
               for c in key_figures.changes(clause)]
    if not changed:
        return sections, 0

    def same(m: re.Match, unit: str, value: float) -> bool:
        try:
            got = units._ko_number(m.group("amount"))
        except ValueError:
            return False
        return m.group("unit") == unit and abs(got - value) <= 1e-6 * max(1.0, value)

    def holds(text: str, unit: str, value: float) -> bool:
        return any(same(m, unit, value) for m in key_figures._VALUE.finditer(text))

    rows = {line for t in report_checks.tables(sections)
            if _DECISION_TABLE.search(" ".join(t.header))
            or _DECISION_TABLE.search(str(sections[t.section].get("heading") or ""))
            for line, _ in t.rows}
    out, fixed = [], 0
    for sec in sections:
        lines = str(sec.get("content") or "").split("\n")
        for i, line in enumerate(lines):
            table_row = line.lstrip().startswith("|")
            if table_row and line not in rows:
                continue
            pieces = [line] if table_row else _sentences(line)
            for piece in pieces:
                new = piece
                for clause, (unit, before, after, _, written) in changed:
                    # the same item: a word of the memo's clause (「간식비」) in the sentence
                    if holds(new, unit, after) or not _about(new, clause):
                        continue
                    for m in reversed(list(key_figures._VALUE.finditer(new))):
                        if same(m, unit, before) and (table_row or _DECIDED_AS.match(new, m.end())):
                            new = new[:m.start()] + written + new[m.end():]
                if new != piece:
                    lines[i] = lines[i].replace(piece, new, 1)
                    fixed += 1
        content = "\n".join(lines)
        out.append({**sec, "content": content} if content != sec.get("content") else sec)
    return out, fixed


def _about(piece: str, clause: str) -> bool:
    from app.services import key_figures

    def stems(text: str) -> set[str]:
        return {w[:2] for w in _words(key_figures._VALUE.sub(" ", text))} - {"합의", "확정", "결정"}

    return bool(stems(clause) & stems(piece))


# ── deadlines given by the meeting or the week ──

#: a deadline the memo gives without a date: 「다음 회의에서」, 「다음 주까지」, 「이번 주 내」
_RELATIVE_DUE = re.compile(
    r"다음\s{0,2}(?:회의|미팅|모임)(?:\s{0,2}(?:에서|까지|전|때))?"
    r"|(?:다음|이번)\s{0,2}주\s{0,2}(?:[월화수목금토일]요일\s{0,2})?(?:까지|내|안|중)"
    r"|차주(?:\s{0,2}(?:까지|중))?")
_DUE_HEAD = re.compile(r"기한|마감|완료\s{0,2}일|언제")
#: a deadline called missing: 「비교표의 기한은 자료에 명시된 기한이 없으므로」
_DUE_ABSENT = re.compile(
    r"(?:기한|마감|제출\s{0,2}일|날짜)[^\n.]{0,40}?"
    r"(?:없|명시되지|명시되어\s{0,2}있지|나와\s{0,2}있지|적혀\s{0,2}있지|정해지지|미정|알\s{0,2}수\s{0,2}없)")


def relative_dues(memo_text: str) -> list[tuple[str, set[str]]]:
    """(deadline as the memo words it, the task's words) for each task the memo ties to the
    next meeting or a week; the next meeting's own dated clause and the people's names aside."""
    met = read(memo_text)
    names = re.findall(r"[가-힣A-Za-z]{2,12}", f"{met.this.attendees} {met.next.attendees}")
    out = []
    for stretch in _stretches(memo_text):
        for clause in re.split(r",\s{1,3}|，", stretch.partition(":")[2] or stretch):
            # 「다음 회의 10/21 화 10시」 is the meeting itself, no task's deadline
            if not (due := _RELATIVE_DUE.search(clause)) or _DAY.search(clause) \
                    or _CLOCK.search(clause):
                continue
            rest = clause[:due.start()] + " " + clause[due.end():]
            for name in names:
                rest = rest.replace(name, " ")
            words = _words(rest)
            phrase = re.sub(r"\s{0,2}(?:에서|때)$", "", due.group(0)).strip()
            if words:
                out.append((phrase, words))
    return out


def _task_of(text: str, dues: list[tuple[str, set[str]]]) -> str:
    flat = _flat(text)
    return next((phrase for phrase, words in dues if any(w in flat for w in words)), "")


def false_dues(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """A task the memo ties to the next meeting or a week (「다음 회의에서 최다은이 비교표
    가져오기」) called without a deadline: a blank 기한 cell takes the memo's words (「다음
    회의」), and a sentence calling its deadline missing goes (its table pointer kept)."""
    from app.services import report_checks

    dues = relative_dues(memo_text)
    if not dues:
        return sections, 0
    out = [dict(s) for s in sections]
    fixed = 0
    for table in report_checks.tables(out):
        due = next((k for k, h in enumerate(table.header) if _DUE_HEAD.search(h)), None)
        if due is None:
            continue
        content = str(out[table.section].get("content") or "")
        for line, row in table.rows:
            if len(row) <= due or not _BLANK_CELL.fullmatch(row[due]):
                continue
            if phrase := _task_of(" ".join(c for k, c in enumerate(row) if k != due), dues):
                row = [*row[:due], phrase, *row[due + 1:]]
                content = content.replace(line, "| " + " | ".join(row) + " |", 1)
                fixed += 1
        out[table.section]["content"] = content
    for sec in out:
        content = str(sec.get("content") or "")
        gone = [p for line in content.split("\n") for p in _sentences(line)
                if _DUE_ABSENT.search(p) and _task_of(p, dues)]
        for piece in gone:
            content = drop(content, piece)
        fixed += len(gone)
        sec["content"] = content
    return out, fixed


def _same(a: str, b: str) -> bool:
    return a == b or bool(a and b and (a in b or b in a))


def dedupe_rows(sections: list[dict]) -> tuple[list[dict], int]:
    """A table row that repeats one above it (the same first cell, every other cell the same
    but one that holds the other's words: 「「다음 회의에서」」 and 「회의 발언「다음 회의에서」」)
    goes; the fuller row stays."""
    from app.services import report_checks

    out = [dict(s) for s in sections]
    gone = 0
    for table in report_checks.tables(out):
        content = str(out[table.section].get("content") or "")
        kept: list[tuple[str, list[str]]] = []
        for line, row in table.rows:
            flat = [re.sub(r"[\s「」『』\"'*]", "", c) for c in row]
            twin = next((k for k, (_, prev) in enumerate(kept)
                         if len(prev) == len(flat) and len(flat) >= 2 and prev[0] == flat[0]
                         and sum(p != c for p, c in zip(prev, flat, strict=True)) <= 1
                         and all(_same(p, c) for p, c in zip(prev, flat, strict=True))), None)
            if twin is None:
                kept.append((line, flat))
                continue
            first, prev = kept[twin]
            if sum(map(len, flat)) > sum(map(len, prev)):
                # the fuller row in the first one's place
                content = content.replace(first, line, 1)
                kept[twin] = (line, flat)
                first = line
            at = content.find(line, content.find(first) + len(first))
            if at > 0 and content[at - 1] == "\n":
                content = content[:at - 1] + content[at + len(line):]
                gone += 1
        out[table.section]["content"] = content
    return out, gone


# ── counts ──

_COUNT_WORDS = ("한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열")
#: a count announcing the items that follow: 「분담한 일은 세 가지입니다」, 「다음 3건」
_ANNOUNCED = re.compile(
    r"(?<![가-힣\d])(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|[1-9]\d?)\s?(가지|건|개)"
    r"(?=입니다|이다|이며|이고|로\s정리|를\s정리|은\s(?:다음|아래)|는\s(?:다음|아래))"
    r"|(?<=다음\s)(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|[1-9]\d?)\s?(가지|건|개)(?![가-힣])")
_LIST_ITEM = re.compile(r"^\s{0,4}(?:[-*•]|\d{1,2}[.)])\s")
_CAPTION_START = re.compile(r"\s{0,4}\*{0,2}\s{0,2}(?:표|그림)\s?\d")
_RULE_LINE = re.compile(r"^\s{0,4}\|?[\s|:-]{3,}$")


def _block_items(block: str) -> int:
    """Rows of a table block (no header or rule line), or items of a list block; 0 otherwise."""
    lines = [ln for ln in block.split("\n") if ln.strip()]
    if lines and all(ln.lstrip().startswith("|") for ln in lines):
        rules = [k for k, ln in enumerate(lines) if _RULE_LINE.match(ln)]
        return len(lines) - rules[0] - 1 if rules and rules[0] >= 1 else 0
    if lines and all(_LIST_ITEM.match(ln) for ln in lines):
        return len(lines)
    return 0


def counted(sections: list[dict]) -> tuple[list[dict], int]:
    """A paragraph announcing N items (「분담한 일은 세 가지입니다」) right before the table or
    list that holds them (its caption between) says the number of items that list holds."""
    out = [dict(s) for s in sections]
    fixed = 0
    for sec in out:
        # blocks at even places, the blank lines between them kept as written
        parts = re.split(r"(\n[ \t]{0,4}\n)", str(sec.get("content") or ""))
        blocks = list(range(0, len(parts), 2))
        for at, k in enumerate(blocks[:-1]):
            block = parts[k]
            found = list(_ANNOUNCED.finditer(block))
            if len(found) != 1 or _LIST_ITEM.match(block) \
                    or block.lstrip().startswith(("|", "#", "```", "**표", "**그림")):
                continue
            nxt = blocks[at + 1]
            if _CAPTION_START.match(parts[nxt]) and at + 2 < len(blocks):
                nxt = blocks[at + 2]
            items = _block_items(parts[nxt])
            m = found[0]
            said = m.group(1) or m.group(3)
            n = int(said) if said.isdigit() else _COUNT_WORDS.index(said) + 1
            if not items or items == n or items > len(_COUNT_WORDS):
                continue
            start = m.start(1) if m.group(1) else m.start(3)
            word = str(items) if said.isdigit() else _COUNT_WORDS[items - 1]
            parts[k] = block[:start] + word + block[start + len(said):]
            fixed += 1
        sec["content"] = "".join(parts)
    return out, fixed


# ── the writer's notes in cells, and pointers at rows gone ──

#: a basis in brackets: 「(기준 2026-10-14)」, 「(2026-10-14 기준)」, 「(기준일: 10/14)」
_BASIS = re.compile(r"[ \t]{0,2}\((?:[ \t]{0,2}기준일?[ \t]{0,2}[:：]?[ \t]{0,2}[^()\n|]{1,24}"
                    r"|[^()\n|]{1,24}[ \t]기준)[ \t]{0,2}\)")
#: a cell telling only where its value came from: 「회의 자료에서 명시」, 「메모에 기재됨」
_SOURCE_CELL = re.compile(
    r"(?:회의[ \t]?)?(?:자료|메모|원문|원본|회의록|기록|발언)"
    r"[ \t]?(?:에서|에|상|에[ \t]?따름|에[ \t]?근거)?[ \t]?(?:명시|기재|확인|근거|언급|표기)"
    r"(?:됨|함|되어[ \t]?있음|된[ \t]?(?:내용|값|정보))?")


#: a bracketed part of a cell, and the memo words it may quote
_BRACKET = re.compile(r"[ \t]{0,2}\(([^()\n|]{2,160})\)")
_QUOTE_IN = re.compile(r"「([^」\n]{1,40})」")
_CHECK = re.compile(r"확인[ \t]?필요")


#: the memo's words quoted as the writer's working: 「원문 표현: "다음 주 금요일"」,
#: 「회의 발언: 「다음 회의에서」」, 「원문 「모레」」
_WORDING = re.compile(
    r"(?:원문|원본|메모|회의[ \t]?발언|발언)(?:[ \t]?(?:표현|문구|표기|기재))?[ \t]?[:：]?[ \t]?"
    r"(?:「[^」\n]{1,60}」|\"[^\"\n]{1,60}\"|“[^”\n]{1,60}”|'[^'\n]{1,60}')")
#: a written date: 「2026-10-23」, 「10/23」, 「10월 23일」
_A_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|(?<!\d)\d{1,2}/\d{1,2}(?!\d)|\d{1,2}월[ \t]?\d{1,2}일")


def _unquoted(cell: str) -> str:
    """`cell` less the memo words it quotes as the original, its separators closed."""
    cut = _WORDING.sub("", cell)
    if cut == cell:
        return cell
    cut = re.sub(r"[ \t]{0,2}[,;·][ \t]{0,2}(?=[,;·]|$)", "", cut)
    return re.sub(r" {2,}", " ", cut).strip(" ,;·—-")


def cell_notes(sections: list[dict], memo_text: str) -> tuple[list[dict], int]:
    """Table cells less the writer's note on its own working: a basis or a note in brackets
    (「(기준 2026-10-14)」, 「2026-10-14 (기준일: … 해석)」) goes, its 「확인 필요」 kept, and a
    cell that only says where its value came from (「회의 자료에서 명시」) or how it was worked
    out (`stated_meeting.self_note`) reads 「-」. The memo words a cell quotes as the original
    (「원문 표현: "다음 주 금요일"」) go from a row whose due is a date; a row with no date
    keeps them as its only due (「원문 「다음 주 금요일」」). The first cell, which names the
    row, stays."""
    from app.services import report_checks, stated_meeting

    def note(text: str) -> bool:
        return bool(_SOURCE_CELL.fullmatch(text) or stated_meeting.self_note(text, memo_text))

    def unbracketed(cell: str) -> str:
        # a value with the note on its working in brackets: the value, a 「확인 필요」 kept
        def cut(m: re.Match) -> str:
            inner = m.group(1).strip()
            if not note(inner):
                return m.group(0)
            return " (확인 필요)" if _CHECK.search(inner) else ""
        return re.sub(r" {2,}", " ", _BRACKET.sub(cut, cell)).strip()

    memo_flat = squash(memo_text)
    out = [dict(s) for s in sections]
    fixed = 0
    for table in report_checks.tables(out):
        content = str(out[table.section].get("content") or "")
        _, due = report_checks._owner_due(table.header)
        for line, row in table.rows:
            cells = list(row)
            dated = due is not None and due < len(cells) and bool(_A_DATE.search(cells[due]))
            for k in range(1, len(cells)):
                cell = cells[k]
                if len(cell) <= 300:
                    cell = unbracketed(_BASIS.sub("", cell).strip())
                if dated and k != due:
                    cell = _unquoted(cell)
                bare = cell.strip(" *.")
                if bare and note(bare):
                    # the memo's own words it quotes stay as the due a dateless row has
                    said = [q for q in _QUOTE_IN.findall(bare) if squash(q) in memo_flat]
                    cell = " ".join(f"원문 「{q}」" for q in said) if said and not dated else "-"
                cells[k] = cell or "-"
            if cells != list(row):
                content = content.replace(line, "| " + " | ".join(cells) + " |", 1)
                fixed += 1
        out[table.section]["content"] = content
    return out, fixed


#: a clause pointing at the other items as open: 「나머지 항목은 확인이 필요합니다」
_REST_OPEN = re.compile(
    r"(?:나머지|그[ \t]?외|기타|남은)[ \t]?(?:항목|과제|업무|일|사항|안건|행)[가-힣]{0,2}[ \t]"
    r"[^,.\n]{0,20}(?:(?:확인|확정|결정|지정)(?:이|가)?[ \t]?필요|미정|정해지지[ \t]?않)")
#: a clause's end again once the clause after it went: 「작성하며」 → 「작성합니다」
_RE_END = ((re.compile(r"([었았였했])(?:으며|고)$"), r"\1습니다"),
           (re.compile(r"하(?:며|고)$"), "합니다"), (re.compile(r"되(?:며|고)$"), "됩니다"))


def rest_open(sections: list[dict]) -> tuple[list[dict], int]:
    """A clause calling the other items open (「…, 나머지 항목은 확인이 필요합니다(표 2)」) while
    no table row holds an open cell (the row it pointed at went) goes; the sentence before it
    ends again and keeps its table pointer, a sentence of that clause alone goes."""
    from app.services import stated_meeting, stated_policy

    rows = stated_meeting.open_rows(sections)
    if rows is None or rows:
        return sections, 0
    out = [dict(s) for s in sections]
    gone = 0
    for sec in out:
        content = str(sec.get("content") or "")
        for piece in stated_policy.pieces(content):
            if piece.lstrip().startswith("|") or not _REST_OPEN.search(piece[:400]):
                continue
            # the sentence's last clause only
            clauses = piece.split(", ")
            at = len(clauses) - 1
            if not _REST_OPEN.search(clauses[at][:400]):
                continue
            pointer = "".join(_POINTER.findall(clauses[at]))
            if at == 0:
                plain = f"정리하면 다음과 같습니다{pointer}." if pointer else ""
                content = content.replace(piece, plain, 1) if plain else drop(content, piece)
                gone += 1
                continue
            last = clauses[at - 1]
            for rx, polite in _RE_END:
                if rx.search(last):
                    clauses[at - 1] = rx.sub(polite, last)
                    break
            else:
                continue
            content = content.replace(piece, ", ".join(clauses[:at]) + f"{pointer}.", 1)
            gone += 1
        sec["content"] = content
    return out, gone


# ── memo lines copied as they stand, and a phrase said over and over ──

#: a sentence finished as prose: 「…했습니다.」, 「…한다.」, 「…인가요?」, a pointer after it
_FINISHED = re.compile(
    r"(?:다|요|까|죠|오)[.!?]?[」』\"')]{0,2}(?:[ \t]?\([^()\n]{1,12}\))?[.!?]?$")
#: a memo's noun-form end inside a sentence: 「…하기로 함 회의록상…」, 「…있었음, …」
_NOUN_END = re.compile(r"(?:기로|못|안)[ \t]?함(?=[ \t,])|[었았였했있됐]음(?=[ \t,])")
#: the memo's signs: 「A vs B」, 「→」
_SIGN = re.compile(r"(?<![A-Za-z])vs\.?(?![A-Za-z])|[→⇒]")
_QUOTED = re.compile(r"「[^」\n]{0,80}」|『[^』\n]{0,80}』|\"[^\"\n]{0,80}\"")
_LIST_MARK = re.compile(r"^[ \t]{0,4}(?:[-*•]|\d{1,2}[.)])[ \t]")
#: the memo's agenda label before its line: 「안건2 예산:」
_AGENDA_LABEL = re.compile(r"(?:안건|의제)[ \t]?\d{1,2}[ \t]?[^:：\n]{0,12}[:：]")
_SHINGLE = 6


def squash(text: str) -> str:
    return re.sub(r"[\s,.:;·!?()「」『』\"'*]", "", text or "").lower()


def _covered(piece: str, memo: str) -> float:
    """The part of `piece` (spaces and marks out) lying in runs it shares with the memo."""
    flat, source = squash(piece), squash(memo)
    if len(flat) < _SHINGLE + 2:
        return 0.0
    grams = {source[k:k + _SHINGLE] for k in range(len(source) - _SHINGLE + 1)}
    hit = [False] * len(flat)
    for k in range(len(flat) - _SHINGLE + 1):
        if flat[k:k + _SHINGLE] in grams:
            hit[k:k + _SHINGLE] = [True] * _SHINGLE
    return sum(hit) / len(flat)


def copied(piece: str, memo: str) -> bool:
    """A prose sentence that is the memo's own line in its shorthand (「안건2 예산: … 합의.」,
    「점심 메뉴 얘기 … 있었음.」): most of it the memo's words in a row, its end no finished
    sentence or a 「vs」, an arrow or a noun-form end inside it; quotes aside. A sentence with a
    shorthand end inside (「…하기로 함 회의록상…」) is one too, the memo's or not."""
    body = (piece or "").strip()
    if not body or not memo or len(body) > 400 or body.startswith(("|", "#")) \
            or _LIST_MARK.match(body):
        return False
    bare = _QUOTED.sub("", body)
    finished = bool(_FINISHED.search(bare))
    if finished and _NOUN_END.search(bare):
        return True
    if finished and not (_SIGN.search(bare) or _AGENDA_LABEL.search(bare)):
        return False
    return _covered(bare, memo) >= 0.6


def copies(sections: list[dict], memo: str, skip_heading: re.Pattern | None = None
           ) -> list[tuple[int, str]]:
    """(section, sentence) of every prose sentence `copied` from the memo."""
    from app.services import stated_policy

    out = []
    for index, sec in enumerate(sections):
        if skip_heading is not None and skip_heading.search(str(sec.get("heading") or "")):
            continue
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if copied(piece, memo):
                out.append((index, piece))
    return out


COPY_WHY = ("메모 줄을 그대로 옮겼다(메모의 줄임말 「…함」·「…있었음」·「A vs B」·안건 라벨). 이 "
            "문장을 회의록 문장(합니다체)으로 고쳐 써라: 메모의 이름·수치·결정은 그대로 두고 "
            "새 내용은 보태지 마라. 앞뒤 문장이 이미 같은 말을 하면 원래 문장을 그대로 돌려줘라.")

#: a clause long enough to count as a phrase said again
_PHRASE = 8
#: a phrase said this often in one document reads as pasted
_TIMES = 3
#: a cell saying the item is still open
_UNDECIDED = re.compile(r"결정[ \t]?(?:못|안|하지[ \t]?못)|미정|미결|보류|결론을[ \t]?(?:내리지|못)"
                        r"|정하지[ \t]?못")


def _clauses(piece: str) -> list[str]:
    return [c for c in re.split(r"[,.;]", piece) if len(squash(c)) >= _PHRASE]


def _said_over(sections: list[dict]) -> set[str]:
    """Phrases (squashed clauses) that `_TIMES` or more sentences, list items or cells say."""
    from app.services import report_checks, stated_policy

    seen: dict[str, int] = {}
    for sec in sections:
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if piece.startswith("|"):
                continue
            for flat in {squash(c) for c in _clauses(piece)}:
                seen[flat] = seen.get(flat, 0) + 1
    for table in report_checks.tables(sections):
        for _, row in table.rows:
            for cell in row[1:]:
                for flat in {squash(c) for c in _clauses(cell)}:
                    seen[flat] = seen.get(flat, 0) + 1
    return {flat for flat, n in seen.items() if n >= _TIMES}


def repeats(sections: list[dict]) -> list[tuple[int, str, str]]:
    """(section, sentence, phrase) for each sentence after the first one saying a phrase the
    document says `_TIMES` or more times (「구글폼 vs 네이버폼 논의, 결정 못 함」 four times)."""
    from app.services import stated_policy

    over = _said_over(sections)
    if not over:
        return []
    out, first = [], set()
    for index, sec in enumerate(sections):
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if piece.startswith("|"):
                continue
            for clause in _clauses(piece):
                flat = squash(clause)
                if flat not in over:
                    continue
                if flat in first:
                    out.append((index, piece, clause.strip()))
                    break
                first.add(flat)
    return out


def repeat_why(phrase: str) -> str:
    return (f"「{phrase[:60]}」를 문서에서 이미 여러 번 썼다. 이 문장에서는 그 말을 되풀이하지 "
            "말고 빼라(남는 내용이 없으면 문장째 빼고 빈 문자열로 답하지 말고 원래 문장을 "
            "그대로 돌려줘라).")


def _facts(piece: str, memo: str) -> set[str]:
    """What a sentence holds that the reader needs: its numbers and the memo's people."""
    from app.services import stated_meeting

    found = {m.group(0).replace(",", "")
             for m in re.finditer(r"\d[\d,.]{0,12}", _AGENDA_LABEL.sub("", piece))}
    found |= {name for name in stated_meeting.people(memo) if name in piece}
    return found


def settle_copies(sections: list[dict], memo: str, *, strong: bool) -> tuple[list[dict], int]:
    """What the writer left pasted after its one repair: a sentence said again word for word
    goes; for a writer not strong, a memo line copied or a sentence repeating a phrase said
    over and over goes when the rest of the document holds its numbers and people (the
    first saying stays), and a cell repeating an open item's phrase reads 「미정」."""
    from app.services import report_checks, stated_policy

    out = [dict(s) for s in sections]
    gone = 0
    # the same sentence twice, word for word
    seen: set[str] = set()
    for sec in out:
        content = str(sec.get("content") or "")
        for piece in stated_policy.pieces(content):
            flat = squash(piece)
            if piece.startswith("|") or len(flat) < _PHRASE + 2:
                continue
            if flat in seen:
                content = drop(content, piece)
                gone += 1
            seen.add(flat)
        sec["content"] = content
    if strong:
        return out, gone
    pasted = copies(out, memo) + [(i, p) for i, p, _ in repeats(out)]
    for index, piece in pasted:
        content = str(out[index].get("content") or "")
        if piece not in content:
            continue
        rest = "\n".join(str(s.get("content") or "") for s in out).replace(piece, "", 1)
        if all(fact in rest.replace(",", "") for fact in _facts(piece, memo)):
            out[index]["content"] = drop(content, piece)
            gone += 1
    # a cell repeating an open item's words said over and over, in cells or sentences
    prose = squash("\n".join(ln for s in out for ln in str(s.get("content") or "").split("\n")
                              if not ln.lstrip().startswith("|")))
    tables = report_checks.tables(out)
    said: dict[str, int] = {}
    for table in tables:
        for _, row in table.rows:
            for cell in row[1:]:
                flat = squash(cell)
                said[flat] = said.get(flat, 0) + 1
    over = {flat for flat, n in said.items() if len(flat) >= _PHRASE and _UNDECIDED.search(flat)
            and n + prose.count(flat) >= _TIMES}
    kept: set[str] = set()
    for table in tables:
        content = str(out[table.section].get("content") or "")
        for line, row in table.rows:
            cells = list(row)
            for k in range(1, len(cells)):
                flat = squash(cells[k])
                if flat in over and (flat in kept or flat in prose):
                    cells[k] = "미정"
                kept.add(flat)
            if cells != list(row):
                content = content.replace(line, "| " + " | ".join(cells) + " |", 1)
                gone += 1
        out[table.section]["content"] = content
    return out, gone


#: a 「확인 필요: …」 label line: 「확인 필요: 회의 장소는 자료에 나타나지 않아 …」
_CHECK_LINE = re.compile(
    r"[ \t]{0,4}(?:[-*•※][ \t]{0,2})?\*{0,2}확인[ \t]?필요\*{0,2}[ \t]{0,2}[:：]\*{0,2}[ \t]{0,2}"
    r"(?P<what>[^\n|]{2,160})")
#: a table cell marking its value open
_OPEN_CELL = re.compile(r"확인[ \t]?필요|미정|자료에[ \t]?없|달력상|\[[가-힣 ·]{1,10}\]")
#: a label line's words that name no row
_CHECK_GENERIC = frozenset({"회의", "자료", "확인", "필요", "내용", "사항", "항목", "이번", "해당"})


def restated_checks(sections: list[dict]) -> tuple[list[dict], int]:
    """A 「확인 필요: …」 label line goes when a table row its first word names already
    marks the value open (「| 장소 | 자료에 없음 (미정) |」, a flagged date)."""
    from app.services import report_checks
    from app.services.stated_days import _PARTICLE

    marked = [squash(line) for table in report_checks.tables(sections) for line, row in table.rows
              if any(_OPEN_CELL.search(cell) for cell in row[1:])]
    if not marked:
        return sections, 0
    out = [dict(s) for s in sections]
    gone = 0
    for sec in out:
        lines = str(sec.get("content") or "").split("\n")
        kept = []
        for ln in lines:
            m = _CHECK_LINE.fullmatch(ln)
            tokens = re.findall(r"[가-힣A-Za-z]{2,20}", m.group("what")) if m else []
            words = [w for t in tokens
                     if len(w := _PARTICLE.sub("", t)) >= 2 and w not in _CHECK_GENERIC]
            if words and any(squash(words[0]) in row for row in marked):
                gone += 1
                continue
            kept.append(ln)
        if len(kept) != len(lines):
            sec["content"] = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    return out, gone


#: a line of a marker alone, its sentence gone: 「확인 필요」, 「(확인 필요)」
_MARK_ALONE = re.compile(
    r"[ \t]{0,4}\(?[ \t]{0,2}(?:확인[ \t]?필요|미정)[ \t]{0,2}\)?[ \t]{0,2}[.:]?[ \t]{0,4}")


def bare_marks(sections: list[dict]) -> tuple[list[dict], int]:
    """A paragraph line holding only a marker (「확인 필요」 where a sentence was) goes."""
    out = [dict(s) for s in sections]
    gone = 0
    for sec in out:
        lines = str(sec.get("content") or "").split("\n")
        kept = [ln for ln in lines if not (ln.strip() and _MARK_ALONE.fullmatch(ln))]
        if len(kept) != len(lines):
            gone += len(lines) - len(kept)
            sec["content"] = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    return out, gone


__all__ = ["COPY_WHY", "Meeting", "Memo", "bare_marks", "borrowed_next", "cell_notes", "copied",
           "copies", "counted", "decision_rows", "decisions", "dedupe_rows", "drop",
           "false_absences", "false_dues", "fill_head", "of_request", "overview_day", "read",
           "relative_dues", "repeat_why", "repeats", "rest_open", "restated_checks",
           "settle_copies", "squash"]
