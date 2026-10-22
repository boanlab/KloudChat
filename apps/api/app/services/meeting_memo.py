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


__all__ = ["Meeting", "Memo", "borrowed_next", "counted", "decision_rows", "decisions",
           "dedupe_rows", "drop", "false_absences", "false_dues", "fill_head", "of_request",
           "overview_day", "read", "relative_dues"]
