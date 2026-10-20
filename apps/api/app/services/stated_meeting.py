"""What minutes say about the meeting that the memo never says.

A memo of who agreed to what holds no length of the meeting, no transition period and no
open questions beyond its own: 「회의는 약 20분 만에 … 끝났습니다」, 「기존 양식 사용은 10월
말까지 병행」, 「카드 결제 후 현금 정산할지 … 논의되지 않았습니다」 read as minutes of things
that were said.
A meeting's length or end time stands only when the memo gives that number, a month's part
(「10월 말」) only when the memo names it, and an open question only when most of its words are
the memo's.
"""

from __future__ import annotations

import re

from app.services import stated_policy
from app.services.stated_days import _words

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


def asks_anew(piece: str, material: str) -> bool:
    """An open question (「…정산할지」) most of whose words the memo lacks."""
    if piece.lstrip().startswith("|") or stated_policy.MARK in piece or not _QUESTION.search(piece):
        return False
    novel, words = _novel(piece, _flat(material))
    return novel >= _NOVEL_WORDS and novel >= words * _NOVEL_SHARE


def untraced(piece: str, material: str) -> list[str]:
    """Phrases of a sentence or table row stating a meeting length, an end time, a month's
    part, or an open question (the sentence whole), the memo never gives."""
    if not piece or not (material or "").strip():
        return []
    out = values(piece, material)
    if asks_anew(piece, material):
        out.append(piece.strip().rstrip(".!?。"))
    return out


#: a sentence about the meeting's own length: 「회의는 약 20분 만에 … 끝났습니다」
_OWN_LENGTH = re.compile(r"회의|미팅|모임")


def settle(content: str, piece: str, phrases: list[str], *, strong: bool) -> str:
    """`content` with what stands of `piece` after the writer's repair: a strong writer's is
    marked 「(확인 필요)」 (each value where it stands, a question at its end); anyone else's
    question goes, and so does a sentence about the meeting's own length; a value elsewhere is
    marked."""
    if piece not in content:
        return content
    whole = [p for p in phrases if p == piece.strip().rstrip(".!?。")]
    lengthy = bool(_OWN_LENGTH.search(piece) and phrases and not whole
                   and not any(_MONTH_PART.fullmatch(p) for p in phrases))
    if not strong and (whole or lengthy) and not piece.lstrip().startswith("|"):
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
        for piece in stated_policy.pieces(str(sec.get("content") or "")):
            if said := untraced(piece, material):
                out.append((index, piece, said))
    return out


def why(phrases: list[str]) -> str:
    shown = ", ".join(f"「{p[:60]}」" for p in phrases)
    return (f"{shown}: 회의 자료에 없는 내용이다(회의 시간·종료 시각·기간, 자료가 묻지 않은 "
            "질문). 지어 쓰지 말고 이 부분을 빼라. 문장 전체가 그런 내용이면 문장을 빼라.")


__all__ = ["asks_anew", "findings", "settle", "untraced", "values", "why"]
