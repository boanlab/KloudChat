"""A document's cover title is a name, never the request sentence.

The planner is told not to copy the request; a small model still answers with it, or with no
title, and the document then goes out titled 「각 강에 대한 한줄 요약을 담은 강의계획서
만들어줘」. `echoes_request` catches that; `from_request` is the code's last resort (the document
noun the request names, 「강의계획서」).
"""

from __future__ import annotations

import re

#: an ask's closing verb: 「만들어줘」 「작성해 주세요」 「써 줄래」 「정리 부탁해」
_ASK_ENDING = re.compile(
    r"(?:만들|작성|써|쓰|정리|요약|준비|짜|구성|생성|해)\S{0,4}\s{0,2}(?:줘|주세요|주십시오|줄래|"
    r"줄\s{0,2}수|달라|봐)\S{0,3}\s*[.!?]?$"
    r"|부탁(?:해|합니다|드립니다|드려요)?\s*[.!?]?$"
    r"|^(?:please|make|write|create|draft)\b",
    re.I,
)
#: what a document is called: the noun an ask ends on
_DOC_NOUN = re.compile(
    r"([가-힣A-Za-z0-9·]{0,12}?(?:계획서|보고서|기획서|기획안|제안서|안내문|공문|회의록|리포트|레포트|"
    r"요약본|정리본|매뉴얼|가이드|지침|명세서|목록|일람표|소개서|발표\s?자료|교안|지도안|"
    r"syllabus|report|plan|proposal|summary))(?:을|를|로|으로)?\s{0,2}"
    r"(?:만들|작성|써|쓰|정리|준비|짜|구성|생성|부탁)",
    re.I,
)


def _flat(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (text or "").lower())


def echoes_request(title: str, request: str) -> bool:
    """Whether `title` is no title: empty, the request itself, or an ask (「…만들어줘」)."""
    words = " ".join((title or "").split())
    flat, asked = _flat(words), _flat(request)
    if not flat:
        return True
    if _ASK_ENDING.search(words):
        return True
    return len(flat) >= 10 and flat in asked and len(flat) >= 0.6 * len(asked)


def from_request(request: str) -> str:
    """The document noun the request names (「강의계획서」), or ""."""
    found = _DOC_NOUN.findall(" ".join((request or "").split()))
    if not found:
        return ""
    return found[-1].strip()


__all__ = ["echoes_request", "from_request"]
