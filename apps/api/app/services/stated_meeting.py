"""What minutes say about the meeting that the memo never says.

A memo of who agreed to what holds no length of the meeting, no transition period and no
open questions beyond its own: 「회의는 약 20분 만에 … 끝났습니다」, 「기존 양식 사용은 10월
말까지 병행」, 「카드 결제 후 현금 정산할지 … 논의되지 않았습니다」 read as minutes of things
that were said.
A meeting's length or end time stands only when the memo gives that number, a month's part
(「10월 말」) only when the memo names it, and an open question only when most of its words are
the memo's. Nor does a memo hold the writer's guesses (「보고될 가능성이 높습니다」), an
agenda for the next meeting it never sets (「진행 상황을 공유할 것입니다」), notes that a detail
it had no reason to give is missing (「선정 기준이 회의 자료에 제시되지 않았습니다」), a promise
to tell more later (「확정되는 대로 안내합니다」), a part of the day (「오전 시간대에」) or a
mail's 「제목:/본문:」 lines. Something left open (「집행 담당자가 지정되지 않았습니다」) stands
only when the memo leaves it open or a 미정 cell of the minutes' own tables holds it, and no
sentence calls every owner and deadline set while a cell says 미정. Nor does a memo give the
writer's advice (「… 작성하는 것이 좋습니다」), what follows if a task is not done (「미제출 시 …
지연되어」), an 오전·오후 it never puts before its own 「10시」, or the next meeting's agenda in a
table or in any sentence that speaks of the next meeting. Nor does it hold the writer's note on
what it kept out of the minutes (「… 본문에 반영하지 않았습니다」).
"""

from __future__ import annotations

import re

from app.services import captions, meeting_memo, stated_policy
from app.services.stated_days import _GENERIC, _PARTICLE, _words

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
#: a part of the day the memo may never give: 「오전 시간대에」, 「오후 중」 (not 「오전 10시」)
_DAYPART = re.compile(r"(오전|오후|아침|저녁|새벽)(?![ \t]?\d)(?=[ \t]?(?:시간대|중|에|부터|내내))")
#: 오전·오후 before a clock: 「오전 10시」
_CLOCK_PART = re.compile(r"(오전|오후)[ \t]?(\d{1,2})[ \t]?시")
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


#: syllables ending in ㄹ (「될」, 「달라질」) but 「할」: the verb form before 「수 있」
_RIEUL = "".join(chr(c) for c in range(0xAC00 + 8, 0xD7A4, 28) if chr(c) != "할")
#: a guess, not a record: 「가능성이 높습니다」, 「포함될 것으로 보입니다」, 「달라질 수 있습니다」
_GUESS = re.compile(
    r"가능성이\s?(?:높|크|있)|것으로\s?(?:보입니다|보인다|보여|보이며|보이고|예상|전망|예측|추정)"
    rf"|[{_RIEUL}]\s?수(?:도)?\s?있(?:으나|지만|습니다|다(?![가-힣]))|수\s?있을\s?것"
    # what someone may do (「사용할 수 있습니다」) is no guess; 「포함할 수 있으나」 is
    r"|할\s?수\s?있(?:으나|지만)|할\s?수도\s?있"
    r"|(?:예상|전망|추정)(?:됩니다|된다|되며)|듯(?:합니다|하다)|것\s?같(?:습니다|다)")
#: the writer's advice: 「작성하는 것이 좋습니다」, 「문의하시는 것이 적절합니다」
_ADVICE = re.compile(
    r"(?:것이|게|것도)\s?(?:좋|바람직|적절|필요합니다|필요하다)|(?:바람직|적절)(?:합니다|하다)"
    r"|필요가\s?있(?:습니다|다|음)")
#: what follows a task left undone: 「미제출 시 … 지연되어」, 「제출하지 않으면」
_IF_NOT = re.compile(
    r"(?:하|되)?지\s?않(?:으면|을\s?경우)|(?:안|못)\s?(?:하|되|내)면"
    r"|(?:미(?!팅)[가-힣]{1,3}|누락|지연|불참)\s?시(?=[\s,])"
    r"|(?:지연|누락|미제출|불참|연기)(?:될|되는|할|하는)\s?경우")
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
#: …about the head of the minutes, which no table row holds
_HEAD_FIELD = re.compile(r"일시|장소|연도|요일|참석|불참|시각|회의\s?(?:날짜|일자|시간)")
#: a promise to tell more later: 「확정되는 대로 안내합니다」, 「추후 공지할 예정입니다」
_PROMISE = re.compile(
    r"(?:확정|결정|정리)되는\s?대로|정해지는\s?대로|추후\s?(?:안내|공지|공유|전달|알려)"
    r"|(?:안내|공지)(?:하겠|드리겠|드립니다|할\s?예정|될\s?예정)|알려\s?드리")
_PROMISE_SAID = re.compile(r"안내|공지|대로|알려")
#: something called left open: 「담당자가 회의에서 지정되지 않았습니다」, 「확정 날짜가 없습니다」
_UNSET = re.compile(
    r"(?:지정|배정|확정|결정|정해|정해지|정하|합의|논의|언급|제시|명시)(?:되|하)?지\s?(?:않|못)"
    r"|(?:날짜|기한|담당자?|일정|금액)(?:가|이|은|는)?\s?(?:없|미정)")
#: the writer's own flag on something but an owner or a deadline (those the skill's 「※」 line
#: flags): 「배포 방법은 확인 필요합니다」, 「확인 필요: 포스터 배포 방법」
_FLAG = re.compile(r"(?:확인|확정|정의|협의)(?:이|가)?\s?필요|결정할\s?부분")
#: the memo leaving something open: 「결정 못 함」, 「추후 검토」, 「미정」
_OPEN_SAID = re.compile(
    r"미정|미결|못\s?함|못\s?했|보류|추후|나중에|다음\s?(?:회의|미팅)|재논의|확인\s?(?:필요|요망|중)"
    r"|\?|논의\s?중|검토\s?(?:중|예정|하기로)|검토해|볼게|모름|불명|안\s?됨|않음|안\s?정함"
    r"|이번\s?주\s?안")
#: words of being open, which name nothing left open
_OPEN_WORDS = frozenset(("미정", "재논의", "여부", "확정", "결정", "지정", "아직"))
#: a table cell left open
_OPEN_CELL = re.compile(r"^\**\s{0,2}(?:미정|확인\s{0,2}필요|\[[^\]\n]{1,20}\])")
#: every owner and deadline called set: 「세 안건 모두 책임자와 일정이 지정되었습니다」
_ALL_SET = re.compile(
    r"(?:모두|모든|전부|각각|빠짐없이)[^\n.]{0,30}(?:담당|책임자|기한|일정|마감)[^\n.]{0,30}?"
    r"(?:지정|배정|확정|명시|정해|정했)(?!(?:되|하)?지\s?(?:않|못))")
#: a mail's lines, when nobody asked for a mail (also inside a code mark or a quote)
_MAIL_LINE = re.compile(
    r"^\s{0,4}(?:[-*>`]\s{0,3}){0,3}(?:\*\*)?(?:제목|본문|수신|발신|참조|subject|body)(?:\*\*)?"
    r"\s?[:：]", re.I)
_MAIL_ASKED = re.compile(r"메일|이메일|공지|안내문|제목|본문")


#: the document itself as a place: 「본문에」, 「회의록에는」, 「표 1에」
_DOC_PLACE = re.compile(
    r"(?:본문|회의록|문서|보고서|본\s?자료|(?:위|아래|이|본)\s?표|표\s?\d{1,2})"
    r"(?:에|에는|에서|에서는|상|으로|로)(?![가-힣])")
#: …and the writer leaving something out of it: 「반영하지 않았습니다」, 「포함되어 있지 않습니다」
_LEFT_OUT = re.compile(
    r"(?:반영|기재|기록|포함|수록|표기|명기|게재|작성|정리)\s?(?:하|되)지\s?않"
    r"|(?:반영|기재|기록|포함|수록|표기|명기)되어\s?있지\s?않|(?:적|싣|넣|담)지\s?않")


def _stretches(material: str) -> list[str]:
    """The memo's sentences: split at full stops (not decimal points), lines and semicolons."""
    return [p for p in re.split(r"(?<!\d)\.(?!\d)|[\n;。]", material or "") if p.strip()]


def guessed(piece: str, material: str) -> bool:
    """A guess (「… 가능성이 높습니다」) none of whose words sits in a memo stretch with doubt."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _GUESS.search(piece):
        return False
    doubts = [_flat(p) for p in _stretches(material) if _MEMO_DOUBT.search(p)]
    return not any(w in d for w in _words(piece) for d in doubts)


#: words that tie nothing to the memo's next-meeting lines: a date, a clock, 「안건」
_DATE_WORD = re.compile(r"\d{1,4}(?:월|일|시|분|년)")
_AGENDA_WORDS = frozenset(("안건", "의제", "사항"))
#: a sentence pointing at a table, which its rows answer for: 「예정 안건은 다음과 같습니다(표 3)」
_POINTER = re.compile(r"(?:다음|아래)과\s?같")


def unset_agenda(piece: str, material: str, *, listed: bool = False) -> bool:
    """A sentence or table row of the next-meeting part, or a sentence elsewhere naming the next
    meeting, about what it will take up (「진행 상황을 공유할 것입니다」; any row of a table
    headed 안건·검토, `listed`) sharing no word but a date with the memo's own lines on the next
    meeting."""
    if stated_policy.MARK in piece or not (listed or _AGENDA.search(piece)) \
            or _POINTER.search(piece):
        return False
    said = [_flat(p) for p in _stretches(material) if _NEXT_SAID.search(p)]
    words = {w for w in _words(piece) - _OPEN_WORDS - _AGENDA_WORDS
             if not _DATE_WORD.match(w) and not w.endswith(("다", "며", "고", "서"))}
    # naming nothing (「기한은 미정이며, 다음 회의에서 재논의가 진행됩니다」) sets no agenda
    return bool(words) and not any(w in s for w in words for s in said)


def advised(piece: str, material: str) -> bool:
    """The writer's advice (「비교 자료는 항목별로 작성하는 것이 좋습니다」) in minutes of a memo
    that advises nothing."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece:
        return False
    return bool(_ADVICE.search(piece)) and not _ADVICE.search(material or "")


def if_not(piece: str, material: str) -> bool:
    """What follows a task left undone (「미제출 시 승인 절차가 지연되어」) in minutes of a
    memo that says no such thing."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece:
        return False
    return bool(_IF_NOT.search(piece)) and not _IF_NOT.search(material or "")


def gap_note(piece: str, material: str) -> bool:
    """A note that the memo gives no detail it had no reason to give: not an owner, a deadline,
    a date or a place of the minutes."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece:
        return False
    return bool(_GAP.search(piece)) and not _FIELD.search(piece) and not _GAP.search(material or "")


def mail_line(piece: str, material: str) -> bool:
    """A 「제목:」 or 「본문:」 line in minutes of a memo that asks for no mail."""
    return bool(_MAIL_LINE.match(piece)) and not _MAIL_ASKED.search(material or "")


def mail_lines(standing: list[str]) -> list[str]:
    """Lines of the agent's or project's instructions that ask for a mail: they let its
    「제목:/본문:」 lines stand, while the rest of the instructions are no memo."""
    return [line for text in standing for line in text.splitlines() if _MAIL_ASKED.search(line)]


def self_note(piece: str, material: str) -> bool:
    """The writer's note on what it kept out of the minutes (「… 이는 회의 안건과 무관하여 본문에
    반영하지 않았습니다」) in minutes of a memo that says no such thing."""
    if piece.lstrip().startswith("|"):
        return False
    # a mark another check put on it (a weekday, a rule) hides no note
    piece = piece.replace(stated_policy.MARK, "")
    return _left_out(piece) and not _left_out(material or "")


def _left_out(text: str) -> bool:
    """A sentence whose predicate leaves something out of the document itself (「… 본문에
    반영하지 않았습니다(표 1)」)."""
    for sentence in re.split(r"(?<=[다요])[.!?]", text or ""):
        tail = _POINTER_TAIL.sub("", sentence.strip())[-30:]
        if _LEFT_OUT.search(tail) and _DOC_PLACE.search(sentence):
            return True
    return False


#: a table pointer at a sentence's end: 「(표 1)」
_POINTER_TAIL = re.compile(
    r"\(\s{0,2}" + captions.EITHER + r"\s{0,2}\d{1,3}\s{0,2}\)\s{0,2}[.!?]?$")


#: agreement stated: 「참석자들이 동의」, 「전원 동의」, 「이의 없이」, 「만장일치」, 「합의」
_CONSENT = re.compile(r"동의|찬성|이의\s?(?:가\s?)?없|만장\s?일치|합의")
#: a memo item: an agenda's stretch (「안건2 예산: … 합의」) or a sentence
_ITEM_BREAK = re.compile(r"(?=안건\s?\d)|(?<!\d)\.(?!\d)|[\n;。]")


def _item_agreed(piece: str, material: str, before: str = "") -> bool:
    """Whether the memo item `piece` speaks of (the items sharing most of its words; those of
    `before`, the sentence it follows, when it shares none (「이는 … 최종 합의 사항입니다」);
    any item when neither does) states an agreement."""
    # the head's attendance (「참석 김지훈(팀장), …」) is no item anyone agrees on
    items = [i for i in _ITEM_BREAK.split(material or "") if i.strip()
             and not re.search(r"참석|불참", i)]
    for said in (piece, before):
        words = {w for w in _words(_CONSENT.sub(" ", said)) if not w.endswith(("다", "며", "고"))}
        scored = [(sum(1 for w in words if w[:2] in _flat(i)), i) for i in items]
        best = max((n for n, _ in scored), default=0)
        if best:
            break
    return any(_CONSENT.search(i) for n, i in scored if n == best)


def consents(piece: str, material: str, before: str = "") -> list[str]:
    """Statements of agreement (「참석자들이 이에 동의하였습니다」, 「전원 동의」) on an item the
    memo states none for: the clause (a cell's part) that holds it, or the sentence whole;
    `before` is the sentence `piece` follows, which a sentence naming no item speaks of."""
    if not _CONSENT.search(piece) or _item_agreed(piece, material, before):
        return []
    if piece.lstrip().startswith("|"):
        return [part.strip() for cell in piece.strip().strip("|").split("|")
                for part in re.split(r"[,，·/]", cell)
                if _CONSENT.search(part) and stated_policy.MARK not in part]
    clauses = [c for c in piece.split(", ") if c.strip()]
    said = [c.strip().rstrip(".!?。") for c in clauses
            if _CONSENT.search(c) and stated_policy.MARK not in c]
    if len(clauses) == 1 or len(said) == len(clauses):
        return [piece.strip().rstrip(".!?。")] if said else []
    return said


#: a clause's end once the clause after it went: 「거론되었으며」 → 「거론되었습니다」
_CLAUSE_END = ((re.compile(r"([었았였했])(?:으며|고)$"), r"\1습니다"),
               (re.compile(r"하(?:며|고)$"), "합니다"), (re.compile(r"되(?:며|고)$"), "됩니다"),
               (re.compile(r"(?:이)?며$"), "입니다"))


#: a clause's own end before a comma: 「…요청했으며」, 「…했고」, 「…했지만」
_CLAUSE_TAIL = re.compile(
    r"(?:[하되이으]며|[하했되됐었았였이않]고|지만|는데|으나|[어아여해]서|므로)$")
#: an agreement verb, and the plain one put for it: 「…메우기로 합의했습니다」 → 「…정했습니다」
_CONSENT_VERB = re.compile(r"(?:합의|동의|찬성)(되었|됐|된|되|하였|했|한|하)")
_PLAIN_VERB = {"되었": "정해졌", "됐": "정해졌", "된": "정해진", "되": "정해지", "하였": "정하였",
               "했": "정했", "한": "정한", "하": "정하"}


def _consent_only(phrase: str) -> bool:
    """Whether a clause says little but the agreement (「참석자 전원이 이에 동의했습니다」), not a
    decision told with an agreement verb (「…야간 연장근무로 메우기로 합의했습니다」)."""
    return len({w for w in _words(phrase) if not _CONSENT.search(w)}) <= 3


def _plain(phrase: str) -> str:
    return _CONSENT_VERB.sub(lambda m: _PLAIN_VERB[m.group(1)], phrase)


#: an agreement word modifying a noun: 「합의한 업무」, 「합의된 사항」, 「동의한 일정」
_AGREED_NOUN = re.compile(r"(?:합의|동의|찬성)(한|된|하신)(?=\s[가-힣])")
_NEUTRAL = {"한": "정한", "하신": "정한", "된": "정해진"}


def consent_cut(piece: str, phrase: str) -> str:
    """`piece` without the agreement `phrase` (a clause, a cell's part), its sentence ended
    again when the clause was the last; "" when it cannot be."""
    if piece.lstrip().startswith("|"):
        cells = []
        for cell in piece.strip().strip("|").split("|"):
            if phrase in cell:
                parts = [p.strip() for p in re.split(r"[,，·/]", cell) if p.strip() != phrase]
                cell = ", ".join(p for p in parts if p) or "-"
            cells.append(cell.strip())
        return "| " + " | ".join(cells) + " |"
    clauses = piece.strip().split(", ")
    bare = [c.rstrip(".!?。") for c in clauses]
    if phrase not in bare:
        return ""
    at = bare.index(phrase)
    if at < len(clauses) - 1 and not _CLAUSE_TAIL.search(phrase):
        # a list's part, not a clause (「합의한 업무는 A와 B, 그리고 C 3건입니다」): only the
        # agreement word modifying a noun goes
        plain = _AGREED_NOUN.sub(lambda m: _NEUTRAL[m.group(1)], piece, count=1)
        return plain if plain != piece else ""
    phrase = clauses[at]
    rest = clauses[:at] + clauses[at + 1:]
    if at == len(clauses) - 1:
        # the sentence's own ending went with its last clause
        end = re.search(r"[.!?。]+$", phrase)
        last = rest[-1]
        for rx, polite in _CLAUSE_END:
            if rx.search(last):
                rest[-1] = rx.sub(polite, last) + (end.group(0) if end else ".")
                break
        else:
            return ""
    plain = ", ".join(rest)
    return plain if len(_words(plain)) >= 3 else ""


#: a sentence's clauses: at commas and after a clause's own end (「…세우고 」, 「…요청했으나 」)
_CLAUSE_SPLIT = re.compile(
    r",\s{1,2}|(?<=[하되이으]며)\s|(?<=[하했되됐었았였이않우]고)\s|(?<=으나)\s"
    r"|(?<=지만)\s|(?<=는데)\s")
#: a person as the doer of a clause: 「박민호가 」, 「이수연은 」, 「팀장이 」
_DOER = r"(?:이|가|은|는|께서)(?=\s)"
#: a memo clause giving someone a task: 「…준비하기로 함」, 「…비교표 가져오기」, 「…맡음」
_ASSIGNED = re.compile(r"기로|키로|맡|담당|가져오|해야|하도록|할\s?것")
#: a task given ahead at a sentence's end: 「…하기로 했습니다」, 「… 예정입니다」
_SET_AHEAD = re.compile(r"(?:기로\s?(?:했|하였|정했)|예정|할\s?것)[가-힣]{0,4}$")
#: a decision told at a sentence's end: 「…하기로 정했습니다」, 「…으로 확정했습니다」
_DECIDED_SAID = re.compile(
    r"(?:기로\s?(?:했|하였|정했|정하였|결정했|결정하였)|확정했|확정하였|결정했|결정하였)[가-힣]{0,4}[.!?。]?$")
#: a state, no act: 「확인이 필요합니다」, 「…에 해당합니다」
_STATE_END = re.compile(r"(?:필요|해당|가능|중요|의미|동일|유사)합니다$")


def _ahead(sentence: str) -> bool:
    """Whether a sentence's predicate is an act still to come (「…집행합니다」, 「…가져옵니다」,
    「…하기로 했습니다」), not the meeting told (「…제시했습니다」) or a state (「…입니다」)."""
    tail = re.sub(r"\s{0,2}\([^()]{0,20}\)\s{0,2}$", "", sentence.strip().rstrip(".!?。 "))
    if _SET_AHEAD.search(tail):
        return True
    if not tail.endswith("니다") or _STATE_END.search(tail):
        return False
    before = tail[-4:-3] if tail.endswith("습니다") else tail[-3:-2]
    if not re.fullmatch(r"[가-힣]", before) or before in "입됩않":
        return False
    # a past or 있다·없다 stem ends in ㅆ or ㅄ (「끝났습니다」, 「있습니다」); 「않습니다」 is none
    return (ord(before) - 0xAC00) % 28 not in (18, 20)


#: what an absentee's bracket gives that names no one: 「정우성(출장)」
_NOT_ROLE = re.compile(r"출장|휴가|병가|외근|연차|교육|사정|불참|미정")


def people(material: str) -> dict[str, list[str]]:
    """The memo's attendees and absentees, each with the words that name them (「김지훈(팀장)」:
    김지훈, 팀장)."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"(?<![가-힣])([가-힣]{3,4})(?:\(([가-힣]{1,6})\))?(?=[\s,，、)]|$)",
                         " ".join(re.findall(r"(?:참석|불참)[^.\n;]{0,120}", material or ""))):
        name, role = m.group(1), m.group(2)
        if name.startswith(("참석", "불참")):
            continue
        role = role if role and not _NOT_ROLE.fullmatch(role) else ""
        out[name] = [name, *([role] if role else [])]
    return out


#: acts of making a thing, one act for a task's wording: 「시안 제작」 for 「시안을 준비」
_MAKING = ("제작", "작성", "준비", "마련", "만들", "가져", "정리", "수립")


def task_given(task: str, texts: list[str]) -> bool:
    """Whether one of `texts` (memo clauses, flattened) gives `task`: it holds a word of the
    task and the task's act (its last word, unless a generic one like 「준비」), or most of the
    task's words."""
    words = _words(task)
    if not words:
        return True
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", re.sub(r"\([^()]{0,40}\)", " ", task))
    act = _PARTICLE.sub("", tokens[-1]).lower() if tokens else ""
    act = act if act in words else ""
    making = act[:2] in _MAKING
    for text in texts:
        hit = sum(1 for w in words if w in text)
        if hit * 2 > len(words) or (hit and (not act or act[:2] in text or (
                making and any(m in text for m in _MAKING)))):
            return True
    return False


def items_of(material: str, names: list[str]) -> list[str]:
    """The memo's items (an agenda's stretch, a sentence) naming any of `names`, flattened."""
    return [_flat(i) for i in _ITEM_BREAK.split(material or "")
            if i.strip() and any(n in i for n in names)]


#: the document's own table or figure, which no one does anything to: 「표 3은 … 보여줍니다」
_PART_NAMED = re.compile(r"(?:표|그림)\s?\d|(?<![A-Za-z])(?:Table|Figure)\s?\d")
#: a verb's ending on a word: 「처리하기로」 → 「처리」, 「포함해」 → 「포함」
_VERB_TAIL = re.compile(r"(?:하기|하는|하여|해서|하며|했|한|할|합|해)$")


def _things(clause: str) -> set[str]:
    """A clause's words less its verbs (「…합니다」, 「…세우고」) and verb endings."""
    out = set()
    for w in _words(clause):
        if w.endswith(("다", "며", "고", "서")):
            continue
        w = _VERB_TAIL.sub("", w)
        if len(w) >= 2 and w not in _GENERIC:
            out.add(w)
    return out


def unowned_task(piece: str, material: str) -> bool:
    """A clause giving a memo person a task (「박민호가 50만원 범위 내에서 지출 계획을 세우고
    집행합니다」), or an act ahead for no one (「포스터에 해당 일정을 포함해 제작합니다」), most of
    whose words no memo item (one naming that person) holds."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _ahead(piece):
        return False
    from app.services import report_checks

    named = people(material)
    tasks = [_flat(c) for c in report_checks._MEMO_CLAUSE.split(material or "")
             if _ASSIGNED.search(c)]
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(piece.strip()) if c.strip()]
    for k, clause in enumerate(clauses):
        # a person the clause names (「박민호가」, 「최다은 위원이」, 「팀장이」)
        doers = [names for names in named.values()
                 if names[0] in clause
                 or any(re.search(rf"(?<![가-힣]){re.escape(n)}{_DOER}", clause) for n in names)]
        if doers:
            names = [n for d in doers for n in d]
            texts = items_of(material, names)
        elif k == len(clauses) - 1:
            # an act for no one on a memo task's thing (「포스터에 … 제작합니다」 by 「이수연이
            # 포스터 시안을 … 준비하기로 함」)
            names, texts = [], tasks
            if _DECIDED_SAID.search(clause):
                # a decision told (「…축소하기로 정했습니다」): any memo item may hold it
                texts = [_flat(i) for i in _ITEM_BREAK.split(material or "") if i.strip()]
            if _NEXT_SAID.search(clause) or _PART_NAMED.search(clause) or not any(
                    w in t for w in _things(clause) if len(w) >= 3 and not re.search(r"\d", w)
                    for t in texts):
                continue
        else:
            continue
        words = {w for w in _things(clause) if w not in names}
        novel = min((sum(1 for w in words if w[:2] not in t) for t in texts), default=len(words))
        if len(words) >= 3 and novel >= 2 and novel * 2 > len(words):
            return True
    return False


def promised(piece: str, material: str) -> bool:
    """A promise to tell more later (「추가 안건은 확정되는 대로 안내합니다」) in minutes of a memo
    that promises nothing of the kind."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece:
        return False
    return bool(_PROMISE.search(piece)) and not _PROMISE_SAID.search(material or "")


def open_rows(sections: list[dict]) -> list[tuple[str, set[str]]] | None:
    """(number cell, words) of the table rows with a 미정, 확인 필요 or bracket cell; None for
    minutes with no table to hold one."""
    from app.services import report_checks

    found = report_checks.tables(sections)
    if not found:
        return None
    out = []
    for table in found:
        for _, row in table.rows:
            if any(_OPEN_CELL.match(c.strip()) for c in row[1:]):
                out.append((row[0].strip(" *"), _words(" ".join(row))))
    return out


def unraised(piece: str, material: str, rows: list[tuple[str, set[str]]] | None) -> bool:
    """Something called left open (「집행 담당자가 회의에서 지정되지 않았습니다」) that no memo
    stretch leaving things open and no open table row shares a word with: an open question the
    memo never raised. A part of the head (연도, 장소) the memo lacks is no question, and with no
    table (`rows` None) nothing is judged."""
    if rows is None or piece.lstrip().startswith("|") or stated_policy.MARK in piece \
            or _HEAD_FIELD.search(piece):
        return False
    if not (_UNSET.search(piece) or (_FLAG.search(piece) and not _FIELD.search(piece))):
        return False
    # what is left open is named by a noun, not a verb (「진행됩니다」)
    words = {w for w in _words(piece) - _OPEN_WORDS if not w.endswith(("다", "며", "고", "서"))}
    if not words or any(w in _flat(p) for p in _stretches(material) if _OPEN_SAID.search(p)
                        for w in words):
        return False
    numbers = set(re.findall(r"(\d{1,2})\s?번", piece))
    return not any(words & said or number in numbers for number, said in rows)


def all_set(piece: str, rows: list[tuple[str, set[str]]] | None) -> bool:
    """A sentence calling every owner and deadline set while a table row holds 미정."""
    return bool(rows) and not piece.lstrip().startswith("|") and stated_policy.MARK not in piece \
        and bool(_ALL_SET.search(piece))


def _marked(piece: str, at: int) -> bool:
    return piece[at:at + 12].lstrip().startswith(stated_policy.MARK)


def values(piece: str, material: str) -> list[str]:
    """A meeting length, an end time or a month's part in `piece` the memo never gives, as
    written (「약 20분」, 「11시」, 「10월 말」), and an 오전·오후 put before the memo's bare
    clock (「오전 10시」); one already marked 「(확인 필요)」 stands."""
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
    out += [m.group(0) for m in _DAYPART.finditer(piece)
            if m.group(0) not in flat and not _marked(piece, m.end())]
    # 「오전 10시」 where the memo says 「10시」 bare
    out += [m.group(0) for m in _CLOCK_PART.finditer(piece)
            if re.search(rf"(?<!\d)(?<!오전)(?<!오후){m.group(2)}시", flat)
            and f"{m.group(1)}{m.group(2)}시" not in flat]
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
    # a verb's stem (「준비하는」) names nothing
    names = [w for t in re.findall(r"[가-힣]{3,12}", piece)
             if len(w := _PARTICLE.sub("", t)) >= 3 and w.lower() in flat and w[-1] not in "하되"]
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


def _goes_whole(piece: str, material: str, next_part: bool,
                rows: list[tuple[str, set[str]]] | None, before: str = "") -> bool:
    """A whole-sentence finding that goes once the memo's names in it stand elsewhere."""
    row = piece.lstrip().startswith("|")
    return (guessed(piece, material) or gap_note(piece, material) or mail_line(piece, material)
            or promised(piece, material) or unraised(piece, material, rows)
            or all_set(piece, rows) or advised(piece, material) or if_not(piece, material)
            or self_note(piece, material) or unowned_task(piece, material)
            or (not row and consents(piece, material, before) == [piece.strip().rstrip(".!?。")]
                and _consent_only(piece))
            or ((next_part or (not row and bool(_NEXT_SAID.search(piece))))
                and unset_agenda(piece, material)))


#: a gap no sentence holds, which every check reads as one space
_LONG_GAP = re.compile(r"[ \t\u3000]{64,}")


def _squeezed(text: str) -> str:
    return _LONG_GAP.sub(" ", text) if len(text) > 64 else text


def untraced(piece: str, material: str, *, next_part: bool = False,
             rows: list[tuple[str, set[str]]] | None = None, before: str = "") -> list[str]:
    """Phrases of a sentence or table row stating a meeting length, an end time, a month's
    part, a part of the day, or (the sentence whole) an open question, a guess, a missing-detail
    note, a promise, something left open the memo and `rows` (the tables' open rows) never
    leave open, every deadline called set beside such a row, or a mail line the memo never
    gives, the writer's advice or what follows an undone task; in the next-meeting part (or a
    sentence naming the next meeting) also an agenda it never sets."""
    if not piece or not (material or "").strip():
        return []
    if (plain := _squeezed(piece)) != piece:
        # the checks read a long gap as one space; the sentence whole stays as written
        whole = plain.strip().rstrip(".!?。")
        return [piece.strip().rstrip(".!?。") if p == whole else p
                for p in untraced(plain, material, next_part=next_part, rows=rows, before=before)]
    out = values(piece, material)
    out += [c for c in consents(piece, material, before) if c not in out]
    whole = piece.strip().rstrip(".!?。")
    if whole not in out and (asks_anew(piece, material)
                             or _goes_whole(piece, material, next_part, rows, before)):
        out.append(whole)
    return out


#: a table's or figure's caption line: 「**표 2. 다음 회의 의제별 검토 사항**」
_CAPTION = re.compile(r"^[ \t]{0,4}\*{0,2}[ \t]{0,2}(?:표|그림|Table|Figure)[ \t]?\d{1,3}[.:]")
#: code marks around nothing once a mail's lines went
_EMPTY_FENCE = re.compile(
    r"(?m)^[ \t]{0,4}`{1,3}[ \t]{0,4}\n(?:[ \t]{0,4}\n){0,8}[ \t]{0,4}`{1,3}[ \t]{0,4}$")
#: a sentence about the meeting's own length: 「회의는 약 20분 만에 … 끝났습니다」
_OWN_LENGTH = re.compile(r"회의|미팅|모임")


def bare_clocks(sections: list[dict], material: str) -> tuple[list[dict], int]:
    """The memo's bare clock back wherever the minutes put 오전·오후 before it (「오전 10시」 →
    「10시」), by code before any repair: a writer told to drop the 오전 drops the clock too."""
    flat = _flat(material)

    def bare(m: re.Match) -> str:
        hour = m.group(2)
        if re.search(rf"(?<!\d)(?<!오전)(?<!오후){hour}시", flat) \
                and f"{m.group(1)}{hour}시" not in flat:
            return m.group(0)[len(m.group(1)):].lstrip()
        return m.group(0)

    out, fixed = [], 0
    for sec in sections:
        content = str(sec.get("content") or "")
        new = _CLOCK_PART.sub(bare, content)
        fixed += new != content
        out.append({**sec, "content": new} if new != content else sec)
    return out, fixed


def settle(content: str, piece: str, phrases: list[str], *, strong: bool,
           material: str = "", elsewhere: str = "", next_part: bool = False,
           rows: list[tuple[str, set[str]]] | None = None) -> str:
    """`content` with what stands of `piece` after the writer's repair: a strong writer's is
    marked 「(확인 필요)」 (each value where it stands, a question at its end); anyone else's
    question goes, and so does a sentence about the meeting's own length, unless it holds a
    name, tool or figure of the memo (then marked: what was said never goes); a guess, an
    agenda, a missing-detail note or a mail line also goes when those names stand `elsewhere`
    in the document (a mail line whoever wrote it, with a code mark left around nothing); a
    value elsewhere is marked."""
    if piece not in content:
        return content
    # the memo's bare clock back, by code: 「오전 10시」 → 「10시」
    bare = [p for p in phrases if _CLOCK_PART.fullmatch(p)]
    if bare:
        plain = piece
        for p in bare:
            plain = plain.replace(p, p[2:].lstrip(), 1)
        content, piece = content.replace(piece, plain, 1), plain
        phrases = [p for p in phrases if p not in bare]
        if not phrases:
            return content
    whole = [p for p in phrases if p == piece.strip().rstrip(".!?。")]
    row = piece.lstrip().startswith("|")
    agreed = [p for p in phrases if _CONSENT.search(p) and (
        p not in whole or (not row and not _consent_only(p)))]
    if agreed and not strong:
        # an agreement the memo never states, by code from anyone's but a strong writer's: a
        # decision told with an agreement verb takes a plain one, a clause of agreement alone
        # goes
        plain = piece
        for p in agreed:
            if not row and not _consent_only(p) and not _CONSENT.search(said := _plain(p)):
                plain = plain.replace(p, said, 1)
            elif p not in whole:
                plain = consent_cut(plain, p) or plain
        if plain != piece:
            content, piece = content.replace(piece, plain, 1), plain
            phrases = [p for p in phrases if p not in agreed]
            whole = [p for p in whole if p in phrases]
            if not phrases:
                return content
    lengthy = bool(_OWN_LENGTH.search(piece) and phrases and not whole
                   and not any(_MONTH_PART.fullmatch(p) for p in phrases))
    kept = any(_memo_words(piece, _flat(material)))
    # a mail's lines are no one's sentences, a strong writer's neither, nor the writer's note
    # on what it left out, whatever names it holds
    mail = bool(whole) and mail_line(piece, material)
    note = bool(whole) and self_note(piece, material)
    if kept and whole and _goes_whole(piece, material, next_part, rows):
        kept = not _said_elsewhere(piece, material, elsewhere)
    if (not strong or mail or note) and (whole or lengthy) and (not kept or note) \
            and not piece.lstrip().startswith("|"):
        # a table pointer the sentence held stays on the sentence before
        lines = meeting_memo.drop(content, piece).split("\n")
        # a line left as a bare list mark, and code marks around nothing
        lines = ["" if re.fullmatch(r"(?:[-*•>]|\d{1,2}[.)])?", ln.strip()) else ln
                 for ln in lines]
        text = _EMPTY_FENCE.sub("", "\n".join(lines))
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    if whole and piece.lstrip().startswith("|"):
        # a row is marked in its last cell
        last = [c.strip() for c in piece.strip().strip("|").split("|") if c.strip()]
        return stated_policy.mark(content, piece, last[-1:])
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
    from app.services import report_checks

    out, rows = [], open_rows(sections)
    found = report_checks.tables(sections)
    heads = [table.header for table in found]
    # rows of an agenda table in the next-meeting part
    listed = {line.strip() for table in found
              if is_next_part(str(sections[table.section].get("heading") or ""))
              and _AGENDA.search(" ".join(table.header)) for line, _ in table.rows}
    for index, sec in enumerate(sections):
        if skip_heading is not None and skip_heading.search(str(sec.get("heading") or "")):
            continue
        nxt = is_next_part(str(sec.get("heading") or ""))
        content = str(sec.get("content") or "")
        captions = [ln for ln in content.split("\n") if _CAPTION.match(ln)]
        before = ""
        for piece in stated_policy.pieces(content):
            # a caption names its table; a table's header row names columns, not an agenda
            if any(piece in ln for ln in captions):
                continue
            if piece.startswith("|") and report_checks.cells(piece) in heads:
                # a header row names columns (「합의 내용」), nothing the meeting did
                continue
            said = untraced(piece, material, next_part=nxt, rows=rows, before=before)
            before = before if piece.startswith("|") else piece
            if not said and piece in listed and unset_agenda(piece, material, listed=True):
                said = [piece.strip()]
            if said:
                out.append((index, piece, said))
    return out


def is_next_part(heading: str) -> bool:
    return bool(_NEXT_PART.search(heading or ""))


def why(phrases: list[str], piece: str = "", material: str = "",
        rows: list[tuple[str, set[str]]] | None = None) -> str:
    shown = ", ".join(f"「{p[:60]}」" for p in phrases)
    told = (f"{shown}: 회의 자료에 없는 내용이다(회의 시간·종료 시각·기간, 자료가 묻지 않은 "
            "질문·추측, 자료가 정하지 않은 다음 회의 안건, 자료가 줄 까닭이 없던 세부가 "
            "없다는 메모, 메일 「제목:/본문:」 줄). 지어 쓴 이 부분만 빼라. 자료에 있는 "
            "이름·도구·수치·결정은 바꾸지 말되, 메모 줄을 그대로 붙이지 말고 회의록 "
            "문장(합니다체)으로 써라. 남는 내용이 없으면 원래 문장을 그대로 돌려줘라.")
    more = [
        *(["자료가 열어 두지 않았고 표의 미정 칸에도 없는 것을 정해지지 않았다고 쓰지 마라."]
          if unraised(piece, material, rows) else []),
        *(["표에 미정 칸이 있으니 모두 정해졌다고 쓰지 마라."] if all_set(piece, rows) else []),
        *(["「확정되는 대로 안내」 같은 약속은 자료에 없다."] if promised(piece, material) else []),
        *(["자료에 없는 오전·오후 같은 때를 쓰지 마라."]
          if any(_DAYPART.fullmatch(p) or _CLOCK_PART.fullmatch(p) for p in phrases) else []),
        *(["회의록은 권고하지 않는다(「…하는 것이 좋습니다」)."]
          if advised(piece, material) else []),
        *(["하지 않으면 어떻게 되는지는 자료에 없다."] if if_not(piece, material) else []),
        *(["회의록에 무엇을 넣지 않았는지는 적지 않는다."]
          if self_note(piece, material) else []),
        *(["자료가 이 안건에 동의·합의를 적지 않았으니 누가 동의했다고 쓰지 마라."]
          if consents(piece, material) else []),
        *(["자료가 이 사람에게 맡기지 않은 일, 아무도 맡지 않은 일을 실행 계획처럼 쓰지 마라 — "
           "자료에 있는 할 일만 자료의 담당자와 함께 쓴다."]
          if unowned_task(piece, material) else []),
    ]
    return " ".join([told, *more])


__all__ = ["advised", "all_set", "asks_anew", "consent_cut", "consents", "findings",
           "gap_note", "guessed", "if_not", "is_next_part", "items_of", "mail_line",
           "mail_lines", "open_rows", "people", "promised", "self_note",
           "settle", "task_given", "unowned_task", "unraised", "unset_agenda",
           "untraced", "values", "why"]
