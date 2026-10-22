"""Limits, approvals and rules a document states as settled that the material never states.

A memo that asks 「최대 4%까지 허용 여부 결재 필요」 holds a request, not a limit: 「내부 허용
한도는 최대 4%」, 「팀 내에서 검토한 최대 허용 인하 폭」, 「안 1은 결재권 한도 초과」, 「승인 없이는
견적서를 발송하지 않습니다」 read as facts the reader will act on. Each kind below is traced as a
whole: a sentence may state a limit only when the material (request, files, sources) states one,
an approval only when the material says it was given, a rule only when the material names it, a
review done within the team only when the material says one was.
"""

from __future__ import annotations

import re

from app.services import units

#: a span stated as the allowed one: 「최대 허용 인하 폭」, 「허용 할인 폭」
_ALLOWED_SPAN = (r"(?:최대|최소)\s?허용\s?(?:(?:인하|할인|증액|감액|조정)\s?)?"
                 r"(?:폭|범위|치|한도|선|수준)|허용\s?(?:인하|할인|조정)\s?폭")
#: (kind, what a sentence states, what the material must state for it)
KINDS: tuple[tuple[str, re.Pattern, re.Pattern], ...] = (
    ("한도", re.compile(r"(?:허용|결재|승인|전결|위임|내부|인하|할인|집행|예산|지출)"
                      r"\s?(?:권\s?)?한도|한도\s?(?:초과|이내|내에서|를\s?넘|를\s?초과)"),
     re.compile(r"한도")),
    ("허용 범위", re.compile(r"허용\s?(?:범위|최대치?|최대\s?폭|상한|하한|기준|치|초과|이내)"
                         rf"|{_ALLOWED_SPAN}"),
     re.compile(rf"허용\s?(?:범위|최대치?|최대\s?폭|상한|하한|기준|치|한도)|{_ALLOWED_SPAN}")),
    ("승인 사실", re.compile(
        r"(?:승인|결재|재가|허가)\s?(?:이|을|를|가)?\s?(?:완료|받았|받은|났|되었|됐|됨|된|되어|마쳤|"
        r"끝났)"),
     re.compile(r"(?:승인|결재|재가|허가)\s?(?:이|을|를|가)?\s?(?:완료|받았|받은|났|되었|됐|됨|된|"
                r"되어|마쳤|끝났)")),
    ("승인 전제", re.compile(
        r"(?:승인|결재|재가|허가)\s?(?:이|가)?\s?(?:완료\s?)?(?:없이는|없이|없으면|"
        r"(?:나|되)지\s?않으면|전에는|전까지는|전까지|이전에는)"),
     re.compile(r"(?:승인|결재|재가|허가)\s?(?:이|가)?\s?(?:완료\s?)?(?:없이|없으면|"
                r"(?:나|되)지\s?않으면|전에는|전까지|이전에는)")),
    ("검토 사실", re.compile(
        r"(?:팀|부서|내부|사내|회사)\s?(?:내에서|안에서|에서|차원에서)?\s?(?:검토|합의)"
        r"(?:한|된|했|하였|해\s?온|를\s?거친|를\s?마친|가\s?끝난)"),
     re.compile(r"(?:검토|합의)(?!\s?(?:요청|필요|부탁|바람|바랍|해\s?주|하여\s?주))")),
    ("규정", re.compile(r"(?:사내|내부|회사|당사|팀)\s?(?:규정|내규|방침|정책|지침)"
                      r"|(?:규정|내규)(?:상|에\s?따라|에\s?따르면|에\s?의해)"),
     re.compile(r"규정|내규|방침|정책|지침")),
)

#: the sentence asks about the point rather than settling it
_ASKS = re.compile(
    r"여부|(?:할|될|인|는|은)지\s|\?|결정(?:이|을|해|하여|해\s?주)|정해\s?주|확인\s?필요"
    r"|\(확인 필요\)|(?:라|이라|다|된다|한다)면\s|(?:되|하)면\s"
    r"|검토(?:가|를)?\s?(?:요청|필요|부탁)|요청(?:합니다|드립니다|드림|함)")
#: whole-line forms that hold no sentence
_SKIP_LINE = re.compile(r"^\s*(?:```|!\[|#|<)")


#: a citation marker: the sentence draws on a cited source
_CITES = re.compile(r"\[\d{1,3}(?:\s?[,\-–]\s?\d{1,3})*\]")


def untraced(sentence: str, material: str, quotes: str = "") -> list[str]:
    """Phrases of `sentence` stating a limit, an approval or a rule the material never states.
    Source `quotes` count for a sentence citing one (a page's rule is no internal one)."""
    if not sentence or _ASKS.search(sentence):
        return []
    if quotes and _CITES.search(sentence):
        material = f"{material}\n{quotes}"
    out = []
    for _, said, given in KINDS:
        if not given.search(material or ""):
            out += [m.group(0).strip() for m in said.finditer(sentence)]
    return list(dict.fromkeys(out))


def pieces(content: str) -> list[str]:
    """Sentences and table rows of a section, verbatim (a table row is one piece)."""
    out = []
    for line in (content or "").split("\n"):
        if not line.strip() or _SKIP_LINE.match(line):
            continue
        if line.lstrip().startswith("|"):
            if not re.fullmatch(r"\s*\|[\s|:-]*\|?\s*", line):
                out.append(line.strip())
            continue
        out += [p.strip() for p in units.SENTENCE_BREAK.split(line) if p.strip()]
    return out


def findings(sections: list[dict], material: str, skip_heading: re.Pattern | None = None,
             quotes: str = "") -> list[tuple[int, str, list[str]]]:
    """(section, sentence or row, untraced phrases) over a document."""
    out = []
    for index, sec in enumerate(sections):
        if skip_heading is not None and skip_heading.search(str(sec.get("heading") or "")):
            continue
        for piece in pieces(str(sec.get("content") or "")):
            if said := untraced(piece, material, quotes):
                out.append((index, piece, said))
    return out


def why(phrases: list[str]) -> str:
    shown = ", ".join(f"「{p}」" for p in phrases)
    return (f"{shown}: 자료에는 이런 한도·승인·규정이 정해져 있다는 말이 없다(요청·질문뿐이다). "
            "정해진 사실처럼 쓰지 말고, 자료가 말한 대로 결재·확인을 요청하는 문장으로 고쳐라.")


MARK = "(확인 필요)"


def mark(content: str, piece: str, phrases: list[str]) -> str:
    """`content` with 「(확인 필요)」 after each phrase of a table row, or at a sentence's end."""
    if piece not in content or MARK in piece:
        return content
    if piece.lstrip().startswith("|"):
        marked = piece
        for phrase in phrases:
            at = marked.find(phrase)
            if at < 0:
                continue
            end = marked.find("|", at)
            end = len(marked) if end < 0 else end
            cell_end = len(marked[:end].rstrip())
            marked = f"{marked[:cell_end]} {MARK}{marked[cell_end:]}"
        return content.replace(piece, marked, 1)
    body, tail = piece, ""
    while body and body[-1] in ".!?。":
        tail = body[-1] + tail
        body = body[:-1]
    return content.replace(piece, f"{body} {MARK}{tail}", 1)


__all__ = ["KINDS", "MARK", "findings", "mark", "pieces", "untraced", "why"]
