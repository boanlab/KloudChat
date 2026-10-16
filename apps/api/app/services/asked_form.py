"""The form a request sets for its own answer: parts, counts, table columns.

Round 8: an agent's always-on skill laid its own frame over a request that named its form. 개조식
보고 turned 「3줄 요약, 영상에서 설명한 핵심 개념, 스터디 토론 질문 2개로」 into a ~임 report
(A08, three models), put 현황·붙임 around 「핵심은 5줄 이내 … 영향 3가지와 준비할 일」
(A02-T2), and a content-plan skill split 「날짜·채널·형식·주제·본문 초안·해시태그를 표로」 into a
schedule and a scored priority table (A13-T2). The skill's text is the person's; what code
can do is see the form the request names and tell the writer the skill's frame yields to it.
"""

from __future__ import annotations

import re

#: a counted part: 「복습 질문 3개」, 「3줄 요약」, 「영향 3가지」, 「기능 3개 블록」, 「5줄 이내」
_COUNTED = re.compile(
    r"(?<![A-Za-z0-9])(?:[가-힣A-Za-z]{1,12}\s{0,2}(?:\d{1,2}|한|두|세|네|다섯)\s{0,2}"
    r"(?:개|가지|줄|안|편|문항|문장|장|블록)"
    r"|(?:\d{1,2}|한|두|세|네|다섯)\s{0,2}(?:줄|문장)\s{0,2}(?:요약|이내|정리))")
#: the columns of an asked table: 「날짜·채널·형식·주제·본문 초안·해시태그를 표로」
_COLUMNS = re.compile(r"[가-힣A-Za-z]{1,10}(?:\s{0,2}[·,/]\s{0,2}[가-힣A-Za-z ]{1,12}){2,12}\s{0,2}"
                      r"(?:을|를|으로|로)?\s{0,2}(?:표로|표\s{0,2}형식|열로|칸으로)")
#: an asked form named outright: 「아래 형식으로」, 「이 순서로」, 「틀에 맞춰」
_NAMED = re.compile(r"(?:형식|양식|순서|구성|틀|목차)(?:으로|로|에\s{0,2}맞춰|대로)")
#: a list of asked parts: 「핵심 개념, 강의에서 든 예시, 복습 질문 3개를 넣어 줘」
_PARTS = re.compile(r"(?:[^\n,]{2,30},\s{0,2}){2,8}[^\n,]{2,30}?(?:을|를|만|까지|도)?\s{0,2}"
                    r"(?:넣어|포함해|담아|붙여|정리해|써|만들어)\s{0,2}(?:줘|주세요|주십시오)")
#: numbered asks under the request: 「1. 경로 두 가지 … 2. 구간마다 …」
_NUMBERED = re.compile(r"(?m)^\s{0,4}\d{1,2}[.)]\s{1,4}\S")


#: a line that asks: 「… 넣어 줘.」, 「… 질문 2개로.」, 「… 요점도 같이.」
_ASKS = re.compile(r"(?:줘|주세요|주십시오|부탁해|바랍니다|로|으로|같이|함께|까지"
                   r"|\d\s?(?:개|안|가지|줄|편))[.!]?\s{0,4}$")
#: a line that opens a list of asks: 「해 줄 것:」, 「다음을 포함한 …」
_ASK_HEAD = re.compile(r"해\s?줄\s?것|할\s?일\s?[:：]|다음을|아래\s?(?:항목|내용)을|포함(?:한|해)")


def _head(text: str) -> str:
    """The lines of the request that ask, without the material pasted with it (a memo, a
    press release): the asking lines, and numbered lines under a line that opens a list of
    asks."""
    out: list[str] = []
    listing = False
    for line in (text or "").split("\n"):
        if _ASK_HEAD.search(line):
            listing = True
        if listing and _NUMBERED.match(line):
            out.append(line)
        elif _ASKS.search(line.strip()):
            out.append(line)
        elif not line.strip():
            listing = listing and bool(out)
    return "\n".join(out)


def asked(text: str) -> list[str]:
    """The parts of the form `text` asks for, as written (at most four); [] when it names
    none."""
    head = _head(text)[:4000]
    found: list[str] = []
    for pattern in (_COLUMNS, _PARTS, _COUNTED, _NAMED):
        for m in pattern.finditer(head):
            piece = re.sub(r"\s+", " ", m.group(0)).strip(" ,")
            if piece and not any(piece in f or f in piece for f in found):
                found.append(piece)
    if len(_NUMBERED.findall(head)) >= 2:
        # the numbered asks themselves are the form
        found.append("번호 붙인 요청 항목")
    return found[:4]


#: told with each skill on a turn whose request names its own form
YIELD_NOTE = (
    "The request names its own form ({form}). That form is the answer's structure: its parts "
    "in its order, its counts, its table columns and the register its purpose needs (a study "
    "note reads as a note, not a report). This skill's frame gives way to it — its sections, "
    "opening lines, default counts, extra tables and output format apply only where the "
    "request leaves the form open. Its accuracy rules still hold."
)


def note(text: str) -> str:
    """The yield note for `text`, or "" when the request names no form."""
    parts = asked(text)
    if not parts:
        return ""
    return YIELD_NOTE.format(form=" · ".join(f"「{p[:60]}」" for p in parts))


__all__ = ["YIELD_NOTE", "asked", "note"]
