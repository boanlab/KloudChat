"""What minutes say about the meeting that the memo never says.

A memo of who agreed to what holds no length of the meeting, no transition period and no
open questions beyond its own: 「회의는 약 20분 만에 … 끝났습니다」, 「기존 양식 사용은 10월
말까지 병행」, 「카드 결제 후 현금 정산할지 … 논의되지 않았습니다」 read as minutes of things
that were said.
A meeting's length or end time stands only when the memo gives that number, a month's part
(「10월 말」) only when the memo names it, and an open question only when most of its words are
the memo's. Nor does a memo hold the writer's guesses (「보고될 가능성이 높습니다」), an
agenda for the next meeting it never sets (「진행 상황을 공유할 것입니다」), notes that a detail
it had no reason to give is missing (「선정 기준이 회의 자료에 제시되지 않았습니다」) or a
mail's 「제목:/본문:」 lines.
"""

from __future__ import annotations

import re

from app.services import stated_policy
from app.services.stated_days import _PARTICLE, _words

#: a length of time: 「약 20분 만에」, 「1시간 동안」, 「30분간」, 「2시간이 소요」
_DURATION = re.compile(
    r"(?:약|대략)?\s?(\d{1,3})\s?(분|시간)\s?"
    r"(?:만에|동안|가량|정도|남짓|에\s?걸쳐|이내|간(?![격단략])|(?:이|가)?\s?소요|(?:이|가)?\s?걸)")
#: an end time: 「11시에 종료」, 「오후 3시 30분경 마쳤다」
_ENDED = re.compile(
    r"(\d{1,2})\s?시(?:\s?(\d{1,2})\s?분)?\s?(?:에|경|쯤|께)?\s?(?:종료|끝났|끝냈|끝나|마쳤|마무리|폐회)")
#: a month's part: 「10월 말까지」, 「11월 초」
_MONTH_PART = re.compile(
    r"(\d{1,2})\s?월\s?(말|초|중순|하순|상순)(?=$|[^가-힣]|까지|에|부터|경|쯤|이|로|은|는|을|과|의)")
#: an open question: 「…할지」, 「…되었는지」, 「… 여부」
_QUESTION = re.compile(r"(?:할|쓸|될|일|인|는|볼|갈|올|줄)지(?![가-힣])|여부")
#: an open question this far from the memo is the writer's own
_NOVEL_WORDS, _NOVEL_SHARE = 5, 0.6


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _novel(sentence: str, flat: str) -> tuple[int, int]:
    """(words the memo lacks, words): a word counts as the memo's when its first two letters
    are in it (stems: 「정산할지」 vs 「정산」)."""
    words = _words(sentence)
    return sum(1 for w in words if w[:2] not in flat), len(words)


#: a guess, not a record: 「가능성이 높습니다」, 「포함될 것으로 보입니다」, 「검토될 수 있으나」
_GUESS = re.compile(
    r"가능성이\s?(?:높|크|있)|것으로\s?(?:보입니다|보인다|보여|예상|전망|예측|추정)"
    r"|(?:될|일)\s?수(?:도)?\s?있(?:으나|지만|습니다|다(?![가-힣]))|할\s?수도\s?있|수\s?있을\s?것"
    r"|(?:예상|전망|추정)(?:됩니다|된다|되며)|듯(?:합니다|하다)|것\s?같(?:습니다|다)")
#: the memo's own doubt (「들음」, 「다음 주쯤」): a guess near it is the memo's
_MEMO_DOUBT = re.compile(r"쯤|들음|들었|듯|같음|아마|추정|예상|가능|수\s?있")
#: a part about the next meeting, and a memo stretch that speaks of it
_NEXT_PART = re.compile(r"다음\s?(?:회의|미팅|모임)|차기|향후\s?일정|next\s?meeting", re.I)
_NEXT_SAID = re.compile(r"다음\s?(?:회의|미팅|모임)|차기|다시\s?(?:만나|모이)|next\s?meeting", re.I)
#: what a meeting takes up: an agenda, a report, a review
_AGENDA = re.compile(r"안건|의제|다룰|다룹|논의|검토|보고|공유|점검|진행\s?상황|발표|선정|"
                     r"결정할|정할|확인할|agenda", re.I)
#: a note that the memo leaves something out (「… 회의 자료에 제시되지 않았습니다」)
_GAP = re.compile(
    r"(?:자료|메모|원문|기록)(?:에|에서|에는|상)?\s?[^\n.]{0,24}?"
    r"(?:제시|명시|포함|기재|언급|나와|나타나|적혀|담겨)[^\n.]{0,10}?(?:않|없)")
#: …about a part of the minutes themselves, which a memo may well leave out
_FIELD = re.compile(r"기한|마감|담당|일시|장소|연도|요일|참석|불참|시각|회의\s?(?:날짜|일자|시간)")
#: a mail's lines, when nobody asked for a mail
_MAIL_LINE = re.compile(
    r"^\s{0,4}(?:[-*]\s{1,3})?(?:\*\*)?(?:제목|본문|수신|발신|참조)(?:\*\*)?\s?[:：]")
_MAIL_ASKED = re.compile(r"메일|이메일|공지|안내문|제목|본문")


def _stretches(material: str) -> list[str]:
    """The memo's sentences: split at full stops (not decimal points), lines and semicolons."""
    return [p for p in re.split(r"(?<!\d)\.(?!\d)|[\n;。]", material or "") if p.strip()]


def guessed(piece: str, material: str) -> bool:
    """A guess (「… 가능성이 높습니다」) none of whose words sits in a memo stretch with doubt."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _GUESS.search(piece):
        return False
    doubts = [_flat(p) for p in _stretches(material) if _MEMO_DOUBT.search(p)]
    return not any(w in d for w in _words(piece) for d in doubts)


def unset_agenda(piece: str, material: str) -> bool:
    """A sentence of the next-meeting part about what it will take up (「진행 상황을 공유할
    것입니다」) sharing no word with the memo's own lines on the next meeting."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _AGENDA.search(piece):
        return False
    said = [_flat(p) for p in _stretches(material) if _NEXT_SAID.search(p)]
    return not any(w in s for w in _words(piece) for s in said)


def gap_note(piece: str, material: str) -> bool:
    """A note that the memo gives no detail it had no reason to give: not an owner, a deadline,
    a date or a place of the minutes."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece:
        return False
    return bool(_GAP.search(piece)) and not _FIELD.search(piece) and not _GAP.search(material or "")


def mail_line(piece: str, material: str) -> bool:
    """A 「제목:」 or 「본문:」 line in minutes of a memo that asks for no mail."""
    return bool(_MAIL_LINE.match(piece)) and not _MAIL_ASKED.search(material or "")


def _marked(piece: str, at: int) -> bool:
    return piece[at:at + 12].lstrip().startswith(stated_policy.MARK)


def values(piece: str, material: str) -> list[str]:
    """A meeting length, an end time or a month's part in `piece` the memo never gives, as
    written (「약 20분」, 「11시」, 「10월 말」); one already marked 「(확인 필요)」 stands."""
    flat = _flat(material)
    out = [piece[m.start():m.end(2)].strip() for m in _DURATION.finditer(piece)
           if f"{m.group(1)}{m.group(2)}" not in flat and not _marked(piece, m.end(2))]
    for m in _ENDED.finditer(piece):
        clock = f"{m.group(1)}시" + (f"{m.group(2)}분" if m.group(2) else "")
        stop = m.end(2) if m.group(2) else m.start() + piece[m.start():].index("시") + 1
        if clock not in flat and not _marked(piece, stop):
            out.append(piece[m.start():stop])
    out += [m.group(0) for m in _MONTH_PART.finditer(piece)
            if f"{m.group(1)}월{m.group(2)}" not in flat and not _marked(piece, m.end())]
    return list(dict.fromkeys(out))


def _memo_words(piece: str, flat: str) -> tuple[bool, bool]:
    """(names or Latin words, numbers) of `piece` that are the memo's own: a name, a tool
    (「구글폼」, 「Slack」) or a figure ties a sentence to what was said."""
    named = any(
        len(w := _PARTICLE.sub("", t)) >= 3 and w.lower() in flat
        for t in re.findall(r"[가-힣]{3,12}", piece)) or any(
        t.lower() in flat for t in re.findall(r"[A-Za-z]{2,30}", piece))
    figures = {t.rstrip(".,").replace(",", "") for t in re.findall(r"\d[\d,.]{0,12}", flat)}
    numbered = any(t.rstrip(".,").replace(",", "") in figures
                   for t in re.findall(r"\d[\d,.]{0,12}", piece))
    return named, numbered


def _said_elsewhere(piece: str, material: str, elsewhere: str) -> bool:
    """Every name, Latin word and figure of the memo's in `piece` also stands in `elsewhere`
    (the rest of the document): taking `piece` out loses nothing that was said."""
    flat, rest = _flat(material), _flat(elsewhere)
    if not rest:
        return False
    names = [w for t in re.findall(r"[가-힣]{3,12}", piece)
             if len(w := _PARTICLE.sub("", t)) >= 3 and w.lower() in flat]
    names += [t.lower() for t in re.findall(r"[A-Za-z]{2,30}", piece) if t.lower() in flat]
    figures = {t.rstrip(".,").replace(",", "") for t in re.findall(r"\d[\d,.]{0,12}", flat)}
    names += [n for t in re.findall(r"\d[\d,.]{0,12}", piece)
              if (n := t.rstrip(".,").replace(",", "")) in figures]
    return all(n.lower() in rest for n in names)


def asks_anew(piece: str, material: str) -> bool:
    """An open question (「…정산할지」) most of whose words the memo lacks and that names none
    of the memo's people or tools."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _QUESTION.search(piece):
        return False
    if _memo_words(piece, _flat(material))[0]:
        return False
    novel, words = _novel(piece, _flat(material))
    return novel >= _NOVEL_WORDS and novel >= words * _NOVEL_SHARE


def untraced(piece: str, material: str, *, next_part: bool = False) -> list[str]:
    """Phrases of a sentence or table row stating a meeting length, an end time, a month's
    part, or (the sentence whole) an open question, a guess, a missing-detail note or a mail
    line the memo never gives; in the next-meeting part also an agenda it never sets."""
    if not piece or not (material or "").strip():
        return []
    out = values(piece, material)
    if (asks_anew(piece, material) or guessed(piece, material) or gap_note(piece, material)
            or mail_line(piece, material) or (next_part and unset_agenda(piece, material))):
        out.append(piece.strip().rstrip(".!?。"))
    return out


#: a sentence about the meeting's own length: 「회의는 약 20분 만에 … 끝났습니다」
_OWN_LENGTH = re.compile(r"회의|미팅|모임")


def settle(content: str, piece: str, phrases: list[str], *, strong: bool,
           material: str = "", elsewhere: str = "", next_part: bool = False) -> str:
    """`content` with what stands of `piece` after the writer's repair: a strong writer's is
    marked 「(확인 필요)」 (each value where it stands, a question at its end); anyone else's
    question goes, and so does a sentence about the meeting's own length, unless it holds a
    name, tool or figure of the memo (then marked: what was said never goes); a guess, an
    agenda, a missing-detail note or a mail line also goes when those names stand `elsewhere`
    in the document (a mail line whoever wrote it); a value elsewhere is marked."""
    if piece not in content:
        return content
    whole = [p for p in phrases if p == piece.strip().rstrip(".!?。")]
    lengthy = bool(_OWN_LENGTH.search(piece) and phrases and not whole
                   and not any(_MONTH_PART.fullmatch(p) for p in phrases))
    kept = any(_memo_words(piece, _flat(material)))
    # a mail's lines are no one's sentences, a strong writer's neither
    mail = bool(whole) and mail_line(piece, material)
    if kept and whole and (
            guessed(piece, material) or gap_note(piece, material) or mail_line(piece, material)
            or (next_part and unset_agenda(piece, material))):
        kept = not _said_elsewhere(piece, material, elsewhere)
    if (not strong or mail) and (whole or lengthy) and not kept \
            and not piece.lstrip().startswith("|"):
        lines = [re.sub(r" {2,}", " ", ln.replace(piece, "", 1)).strip() if piece in ln else ln
                 for ln in content.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if whole:
        return stated_policy.mark(content, piece, whole)
    marked = piece
    for phrase in phrases:
        at = marked.find(phrase)
        if at >= 0 and not _marked(marked, at + len(phrase)):
            marked = f"{marked[:at + len(phrase)]}{stated_policy.MARK}{marked[at + len(phrase):]}"
    return content.replace(piece, marked, 1)


def findings(sections: list[dict], material: str, skip_heading: re.Pattern | None = None
             ) -> list[tuple[int, str, list[str]]]:
    """(section, sentence or row, untraced phrases) over a document."""
    out = []
    for index, sec in enumerate(sections):
        if skip_heading is not None and skip_heading.search(str(sec.get("heading") or "")):
            continue
        nxt = is_next_part(str(sec.get("heading") or ""))
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if said := untraced(piece, material, next_part=nxt):
                out.append((index, piece, said))
    return out


def is_next_part(heading: str) -> bool:
    return bool(_NEXT_PART.search(heading or ""))


def why(phrases: list[str]) -> str:
    shown = ", ".join(f"「{p[:60]}」" for p in phrases)
    return (f"{shown}: 회의 자료에 없는 내용이다(회의 시간·종료 시각·기간, 자료가 묻지 않은 "
            "질문·추측, 자료가 정하지 않은 다음 회의 안건, 자료가 줄 까닭이 없던 세부가 "
            "없다는 메모, 메일 「제목:/본문:」 줄). 지어 쓴 이 부분만 빼라. 자료에 있는 "
            "이름·도구·수치·결정(「구글폼 vs 네이버폼」, 「결정 못 함」)은 자료의 말 그대로 "
            "남겨라.")


__all__ = ["asks_anew", "findings", "gap_note", "guessed", "is_next_part", "mail_line", "settle",
           "unset_agenda", "untraced", "values", "why"]
