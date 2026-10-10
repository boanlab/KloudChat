"""The user guide as a built-in help source: what the chat reads when a question is about
KloudChat itself (「에이전트를 어떻게 만들어?」).

The guide's sections (`app/help/docs.json`, built from `website/docs` by
`scripts/build_help.py`) ship in the image and are searched in process: nothing leaves the
instance. A chat turn whose question asks how to do something in this app gets the best few
sections as a trusted block, so the answer names this app's own screens and buttons instead
of generic advice.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

_DOCS = Path(__file__).resolve().parent.parent / "help" / "docs.json"
_TOP = 3
_BLOCK_CHARS = 4500

#: Things a person makes or sets in this app.
_FEATURES = (
    r"에이전트|스킬|템플릿|양식|테마|디자인|메모리|커넥터|프로젝트|묶음|진행\s{0,2}과정|결과물|"
    r"워크플로|공유|내보내기|사용자\s{0,2}지정|설정|크레딧|모델|관리자|단축키|확인\s{0,2}질문"
)
_HOW = r"어떻게|어디서|어디에서|어디에|어디|뭐야|무엇|방법|하는\s{0,2}법|할\s{0,2}수\s{0,2}있"
_DO = (r"만들|만드|추가|설정|바꾸|바꿔|지우|지워|삭제|연결|공유|쓰|써|사용|켜|꺼|끄|고르|골라|저장|"
       r"내보내|올리|올려")
#: A question about using this app. Bounded patterns only: it runs on a person's text.
_ABOUT_APP = re.compile(
    r"kloud\s{0,2}chat|클라우드\s{0,2}챗|이\s{0,2}(?:앱|서비스|프로그램|사이트)(?:에서|은|는|의)"
    rf"|(?:{_FEATURES})[^\n]{{0,12}}?(?:{_HOW})"
    rf"|(?:{_HOW})[^\n]{{0,12}}?(?:{_FEATURES})[^\n]{{0,12}}?(?:{_DO})"
    r"|how\s{1,3}(?:do|can)\s{1,3}i\s{1,3}[a-z ]{0,40}?\b(?:agent|skill|template|theme|memory"
    r"|connector|project|bundle)s?\b",
    re.I,
)
_WORD = re.compile(r"[A-Za-z]{2,}|[가-힣]+")


def about_app(text: str) -> bool:
    """Whether the question asks how to do something in KloudChat."""
    return bool(_ABOUT_APP.search((text or "")[:2000]))


def _terms(text: str) -> list[str]:
    """Words, and Hangul as two-letter pieces (Korean particles stay out of the way)."""
    out: list[str] = []
    for word in _WORD.findall((text or "").lower()):
        if re.match(r"[가-힣]", word):
            out += [word[i:i + 2] for i in range(max(1, len(word) - 1))]
        else:
            out.append(word)
    return out


@cache
def _index() -> tuple[list[dict[str, Any]], list[Counter], dict[str, float]]:
    try:
        rows = json.loads(_DOCS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [], {}
    bags = [Counter(_terms(f"{r['title']} {r['heading']} {r['heading']} {r['text']}"))
            for r in rows]
    df: Counter = Counter()
    for bag in bags:
        df.update(set(bag))
    n = max(len(rows), 1)
    idf = {term: math.log(1 + n / count) for term, count in df.items()}
    return rows, bags, idf


def search(query: str, *, lang: str = "ko", limit: int = _TOP) -> list[dict[str, Any]]:
    """The guide's best sections for the question, in its language."""
    rows, bags, idf = _index()
    wanted = set(_terms(query))
    if not wanted:
        return []
    scored = []
    for row, bag in zip(rows, bags, strict=True):
        if row["lang"] != lang:
            continue
        score = sum(idf.get(t, 0.0) * min(bag[t], 3) for t in wanted if t in bag)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for _, row in scored[:limit]]


def block(query: str, *, lang: str = "ko") -> str:
    """The trusted block a chat turn about this app reads; "" when nothing fits."""
    found = search(query, lang=lang)
    if not found:
        return ""
    parts = []
    for row in found:
        where = " › ".join(x for x in (row["title"], row["heading"]) if x)
        parts.append(f"## {where}\n{row['text']}")
    body = "\n\n".join(parts)[:_BLOCK_CHARS]
    return (
        "# KloudChat 사용 안내 (이 앱의 도움말)\n"
        "The person is asking how to do something in this app, KloudChat. Answer from these "
        "sections of its user guide: name its own screens, menus and buttons as written here, "
        "in order. Do not give generic advice about other products or prompt-writing when the "
        "guide covers it; when it does not, say the guide does not cover it.\n\n" + body
    )


__all__ = ["about_app", "block", "search"]
