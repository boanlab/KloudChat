"""Korean crisis lines an answer names, set to the official number by code.

A small writer can name the right line with a wrong number (「자살예방 및 정신건강 위기상담:
1599-9195」; the line is 109), which a check could only mark. A number written right after a
crisis line's name is replaced by that line's official number unless the person's text, a file
or a search result gives it; numbers of other lines (생명의전화 1588-9191 and the public ones)
are left alone.

Official numbers (Ministry of Health and Welfare, Ministry of Gender Equality and Family):
109 자살예방상담전화 (the single national line, merging 1393 and the others), 1577-0199
정신건강위기상담전화, 1366 여성긴급전화, 1388 청소년상담전화; 1393 is set to 109.
"""

from __future__ import annotations

import re

#: (line's name as written, official number); the first whose name sits before the number wins
LINES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"자살\s?예방\s?(?:상담\s?)?(?:전화|핫라인|및|상담)|자살\s?(?:위기\s?)?상담\s?전화"
                r"|자살\s?위기\s?상담|suicide\s(?:crisis\s|prevention\s)?(?:line|hotline)", re.I),
     "109"),
    (re.compile(r"정신\s?건강\s?(?:위기\s?)?상담\s?전화|정신\s?건강\s?위기\s?상담"), "1577-0199"),
    (re.compile(r"여성\s?긴급\s?전화"), "1366"),
    (re.compile(r"청소년\s?(?:상담|긴급)\s?전화"), "1388"),
)
#: a local center's own number (「OO구 자살예방센터 02-…」) is not the national line
_CENTER = re.compile(r"센터|지부|지사|복지관")

#: Real lines a crisis answer may name beside the official ones: never replaced.
KNOWN: frozenset[str] = frozenset({
    "109", "1577-0199", "1366", "1388", "1588-9191", "119", "112", "129",
})

#: a phone number: 「1599-9195」, 「02-123-4567」, 「1393」; not part of a longer figure
_NUMBER = re.compile(
    r"(?<![\d.,\-])(?:0\d{1,2}-\d{3,4}-\d{4}|1[5-8]\d{2}-?\d{4}|1\d{2,3})(?!\d|[.,\-]\d|\s?(?:%|년|월|일"
    r"|원|명|개|시간|분|세|회|kg|건|곳|위|점))")
#: how far before a number its line's name may stand: 「자살예방상담전화 」, 「**…위기상담:** 」
_NAME_WINDOW = 40
#: a check's mark on the number, gone once code set it: 「(확인 필요)」, 「, 확인 필요」
_MARK_AFTER = re.compile(r"\s?\(확인\s?필요\)|,\s?확인\s?필요(?=\))")


#: a particle right after the number, set to the new number's last sound (「109으로」 → 「109로」)
_PARTICLE = re.compile(r"(?:으로|로|이|가|을|를|은|는|과|와)(?![가-힣])")
_PAIRS = {"으로": "로", "이": "가", "을": "를", "은": "는", "과": "와"}


def _agree(number: str, particle: str) -> str:
    last = number[-1]
    # read in Korean: 0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔 end in a consonant; 1·7·8 in ㄹ
    closed, rieul = last in "013678", last in "178"
    plain = {v: k for k, v in _PAIRS.items()}.get(particle, particle)
    if plain == "으로":
        return "로" if not closed or rieul else "으로"
    return plain if closed else _PAIRS.get(plain, plain)


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number)


def fix(text: str, evidence: str = "") -> tuple[str, list[str]]:
    """(text, changed): each wrong number right after a crisis line's name set to the official
    one. `evidence`: the person's words, files and search results; a number they give stays."""
    if not text or not _NUMBER.search(text):
        return text, []
    given = {_digits(m.group(0)) for m in _NUMBER.finditer(evidence or "")}
    known = {_digits(n) for n in KNOWN}
    changed: list[str] = []
    out: list[str] = []
    at = 0
    for m in _NUMBER.finditer(text):
        number = m.group(0)
        if _digits(number) in known or _digits(number) in given:
            continue
        head = text[max(0, m.start() - _NAME_WINDOW):m.start()]
        head = head.rsplit("\n", 1)[-1]
        # the name belongs to this number: no other number between them
        ends = [n.end() for n in _NUMBER.finditer(head)]
        if ends:
            head = head[ends[-1]:]
        official = None if _CENTER.search(head) else next(
            (n for name, n in LINES if name.search(head)), None)
        if official is None:
            continue
        out.append(text[at:m.start()])
        out.append(official)
        at = m.end()
        mark = _MARK_AFTER.match(text, at)
        if mark:
            at = mark.end()
        particle = _PARTICLE.match(text, at)
        if particle:
            out.append(_agree(official, particle.group(0)))
            at = particle.end()
        changed.append(f"{number}→{official}")
    if not changed:
        return text, []
    out.append(text[at:])
    return "".join(out), changed


__all__ = ["KNOWN", "LINES", "fix"]
