"""User guide as in-process help source for questions about KloudChat itself."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

from app.core.config import settings

_DOCS = Path(__file__).resolve().parent.parent / "help" / "docs.json"
_TOP = 3
_BLOCK_CHARS = 4500

#: app-only nouns; "how" plus one of these, even words apart, is an app question
_APP_THINGS = (
    r"에이전트|스킬|템플릿|테마|메모리|커넥터|묶음|진행\s{0,2}과정|워크플로|내보내기|"
    r"사용자\s{0,2}지정|크레딧|단축키|확인\s{0,2}질문|아티팩트|"
    # the screen itself
    r"기능|메뉴|버튼|글씨|글자\s{0,2}크기|화면\s{0,2}(?:크게|확대|글자)"
)
#: task words: app question only right beside 「어떻게」
_COMMON_THINGS = r"양식|디자인|프로젝트|결과물|공유|설정|모델|관리자"
_FEATURES = f"{_APP_THINGS}|{_COMMON_THINGS}"
_HOW = (r"어떻게|어디서|어디에서|어디에|어디|뭐야|무엇|방법|하는\s{0,2}법|할\s{0,2}수\s{0,2}있"
        r"|(?:만들|만드|추가할|추가하|쓸|넣을|바꿀|지울|연결할|설정할)\s{0,2}수\s{0,2}있")
_DO = (r"만들|만드|추가|설정|바꾸|바꿔|지우|지워|삭제|연결|공유|쓰|써|사용|켜|꺼|끄|고르|골라|저장|"
       r"내보내|올리|올려")
#: app named as the place
_HERE = (r"여기\s{0,2}(?:서|에서)"
         r"|이\s{0,2}(?:앱|서비스|프로그램|사이트)\s{0,2}(?:에서|은|는|의|으로|로)"
         r"|kloud\s{0,2}chat|클라우드\s{0,2}챗")
#: app-usage question; bounded patterns (runs on user text)
_ABOUT_APP = re.compile(
    r"kloud\s{0,2}chat|클라우드\s{0,2}챗|이\s{0,2}(?:앱|서비스|프로그램|사이트)(?:에서|은|는|의)"
    rf"|(?:{_HERE})[^\n]{{0,40}}?(?:{_HOW}|{_APP_THINGS})"
    rf"|(?:{_APP_THINGS})[^\n]{{0,40}}?(?:{_HOW})"
    rf"|(?:{_COMMON_THINGS})[^\n]{{0,12}}?(?:{_HOW})"
    rf"|(?:{_HOW})[^\n]{{0,12}}?(?:{_FEATURES})[^\n]{{0,40}}?(?:{_DO})"
    r"|how\s{1,3}(?:do|can)\s{1,3}i\s{1,3}[a-z ]{0,40}?\b(?:agent|skill|template|theme|memory"
    r"|connector|project|bundle)s?\b",
    re.I,
)
_WORD = re.compile(r"[A-Za-z]{2,}|[가-힣]+")


def about_app(text: str) -> bool:
    """Whether the question asks how to do something in KloudChat."""
    return bool(_ABOUT_APP.search((text or "")[:2000]))


def _terms(text: str) -> list[str]:
    """Words plus Hangul bigrams (keeps particles out of the way)."""
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
    """Best guide sections for the question, in its language."""
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


#: answer only from the guide
_GROUNDING = (
    "Rules for this answer:\n"
    "- Describe only screens, menus, buttons, settings and features that the guide sections "
    "below name. Never describe a feature they do not mention (no scheduling, automatic "
    "sending, reminders, integrations or automation the guide does not describe), even when "
    "the person asks for one — say plainly that the guide does not describe it.\n"
    "- When the sections do not answer the question, say 「사용 안내에 없는 내용입니다」 (in "
    "English: 'The user guide does not cover this'), then point to the closest page below by "
    "its name (title › heading) and the screen it describes.\n"
    "- Do not give generic advice about other products or prompt-writing instead.\n"
    "- If the message is not actually about using KloudChat (a task such as summarising or "
    "writing), ignore this block and do the task.\n"
)

#: how-to answer length: the path, not the page
_BRIEF = (
    "How to answer:\n"
    "- Brief. First line: where to start (screen › menu › button). Then at most 6 numbered "
    "steps, one short line each. About 250 words in all (Korean: about 600 characters) at "
    "most, unless the person asks for every detail.\n"
    "- No background, no restating the question, no list of every option, no repeated "
    "caveats; one caveat at most, only when it changes what the person does.\n"
    "- For more, end with one line naming the guide page (title › heading), with its link "
    "when one is given below.\n"
    "- Style example (Korean, follow the shape, not the content):\n"
    "  「사용자 지정 › 에이전트 › 새 에이전트에서 만듭니다.\n"
    "  1. 이름과 지시를 적습니다.\n"
    "  2. 진행 과정에서 기본 과정(개조식 보고)을 고릅니다.\n"
    "  3. 파일로 받으려면 「다 쓰면 파일로 내보내기」를 체크하고 형식을 고릅니다.\n"
    "  4. 만들기를 누릅니다.\n"
    "  자세히: 사용 안내 › 에이전트 (링크)」\n"
)


def page_link(path: str, *, lang: str = "ko") -> str:
    """Published guide page URL, or ""."""
    base = (settings.guide_base_url or "").rstrip("/")
    if not base or not path:
        return ""
    page = path[: -len("/index")] if path.endswith("/index") else path
    return f"{base}/{'en/' if lang == 'en' else ''}docs/{page}"

#: guide page names, for questions no section answers
_PAGES = {
    "ko": "사용자 지정 › 에이전트 · 스킬 · 커넥터 / 설정 › 메모리 · 테마 / 프로젝트 / 아티팩트",
    "en": "Customize › Agents · Skills · Connectors / Settings › Memory · Themes / Projects "
          "/ Artifacts",
}


def block(query: str, *, lang: str = "ko") -> str:
    """Trusted help block: best sections, or a no-coverage note so no feature is invented."""
    found = search(query, lang=lang)
    head = "# KloudChat 사용 안내 (이 앱의 도움말)\n" \
        "The person is asking how to do something in this app, KloudChat.\n" + _GROUNDING \
        + _BRIEF
    if not found:
        return (head + "\nThe user guide has no section on this question. Say that the "
                "guide does not cover it and name where in the app to look: "
                + _PAGES.get(lang, _PAGES["ko"]) + "\n")
    parts = []
    for row in found:
        where = " › ".join(x for x in (row["title"], row["heading"]) if x)
        link = page_link(row.get("path", ""), lang=lang)
        parts.append(f"## {where}\n" + (f"Guide page: {link}\n" if link else "") + row["text"])
    body = "\n\n".join(parts)[:_BLOCK_CHARS]
    return head + "Answer from these sections of its user guide, naming its own screens, " \
        "menus and buttons as written here, in order.\n\n" + body


__all__ = ["about_app", "block", "page_link", "search"]
