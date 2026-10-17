"""Notes the server adds under an answer, and taking them back off.

A stored answer keeps the server's notes (the reader saw them), but the next turn's model must
not read them as its own words: sent back as history, they are copied into the next answer and
then added again — every note twice.
"""

from __future__ import annotations

import re

#: `agent.run_turn`'s notes when the web search gave nothing usable.
WEB_NONE_KO = ("\n\n_웹 검색이 쓸 만한 결과를 주지 않아 이 답은 검색으로 확인하지 못했습니다. "
               "서지·수치·최신 사항은 확인이 필요합니다._")
WEB_MOSTLY_KO = ("\n\n_웹 검색 결과가 대부분 질문과 무관해 이 답은 충분히 확인되지 않았습니다. "
                 "서지·수치·최신 사항은 확인이 필요합니다._")
WEB_NONE_EN = ("\n\n_The web search gave no usable results, so this answer was not checked "
               "against it. References, figures and recent facts need checking._")
WEB_MOSTLY_EN = ("\n\n_Most web search results were unrelated to the question, so this answer "
                 "is not fully checked. References, figures and recent facts need checking._")
WEB_NOTES = (WEB_NONE_KO, WEB_MOSTLY_KO, WEB_NONE_EN, WEB_MOSTLY_EN)

#: `check_stages._advice_note`'s closing line (its values vary).
_ADVICE = re.compile(
    r"\n*^확인 필요: [^\n]*— 검색 결과나 주신 내용으로 확인하지 못했으니 "
    r"공식 기관이나 전문가에게 확인하세요\.[ \t]*$", re.M)
_WEB = re.compile("|".join(r"\n*" + re.escape(note.strip()) for note in WEB_NOTES))


def strip(text: str) -> str:
    """`text` without the server's notes, for sending a stored answer back as history."""
    if not text:
        return text
    out = _ADVICE.sub("", _WEB.sub("", text))
    return out.rstrip() if out != text else text


#: The answer saying itself that it could not confirm (「이번 검색에서 … 확인하지 못했으니」,
#: 「not something I verified in this session」): no note repeats it.
_SAYS_UNCONFIRMED = re.compile(
    r"확인(?:하지|할 수|되지)\s?(?:못|않|없)|확인\s?못\s?(?:했|함|한)|찾지\s?못했"
    r"|검색(?:으로|에서|결과[로에]?)?[^.\n]{0,20}(?:나오지\s?않|없었)|"
    r"(?:could|can)(?:not|n't) (?:confirm|verify|find)|not (?:something I )?verified|"
    r"unable to (?:confirm|verify)|did not (?:confirm|verify|find)", re.I)


def says_unconfirmed(text: str) -> bool:
    """Whether the answer itself already says what it could not confirm."""
    return bool(_SAYS_UNCONFIRMED.search(text or ""))


def has_web_note(text: str) -> bool:
    """Whether `text` already carries one of the web-search notes (the model echoed one)."""
    return any(note.strip() in (text or "") for note in WEB_NOTES)
