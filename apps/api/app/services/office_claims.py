"""What a chat turn said it did to an office document, held to what it saved: a reply that
says it wrote, filled or changed the document while no version was saved gets a sentence
saying the document is as it was; a reply that says it could not while versions were saved
gets the versions and what changed in them. Written from the tools' own record (`ToolContext`
`hwp`/`docx`/`pptx` `saved`), every model alike."""

from __future__ import annotations

import re
from typing import Any

#: a sentence claiming the document changed: 「작성했습니다」, 「표로 바꿨습니다」, 「채웠습니다」
_CLAIMS = re.compile(
    r"(?:작성|수정|변경|추가|삭제|반영|저장|정리|교체|이동|복제|적용)(?:을|를)?\s{0,2}(?:했|하였|완료했)"
    r"|(?:고쳤|바꿨|바꾸었|넣었|채웠|만들었|옮겼|지웠|뺐|붙였|늘렸|줄였|나눴|합쳤|띄웠)"
    r"|\b(?:updated|changed|added|filled|wrote|written|replaced|moved|inserted|removed|saved)\b",
    re.I,
)
#: what a claim is about: the document or a part of it
_ABOUT = re.compile(
    r"문서|문단|장|절|표|칸|빈칸|슬라이드|공문|보고서|파일|제목|머리글|바닥글|그림|쪽|항목|"
    r"\b(?:document|slide|table|paragraph|section|cell|file)s?\b", re.I)
#: a sentence saying it could not
_FAILED = re.compile(r"못했|실패|할 수 없|불가능|\b(?:could not|couldn't|failed|unable)\b", re.I)
#: what each kind is called in the note
_NAMES = {"hwp": "한글 문서", "docx": "Word 문서", "pptx": "PowerPoint 문서",
          "report": "웹 보고서", "deck": "웹 슬라이드"}


def _state(ctx: Any, kind: str) -> dict:
    """The tools' record of a kind: the office ones on the context, the web ones in its
    sandbox (`tools/web_doc`)."""
    if kind in ("report", "deck"):
        return (getattr(ctx, "sandbox", None) or {}).get(f"_{kind}") or {}
    return getattr(ctx, kind, None) or {}


def saved_by(ctx: Any) -> list[tuple[str, dict]]:
    """(kind, record) for every version the office tools saved this turn."""
    out = []
    for kind in _NAMES:
        for record in _state(ctx, kind).get("saved") or []:
            out.append((kind, record))
    return out


def note(reply: str, ctx: Any, offered: set[str]) -> str:
    """The sentence to add to `reply`, or `` when it says what happened. `offered`: the office
    kinds whose tools the turn had."""
    if not offered:
        return ""
    saved = saved_by(ctx)
    sentences = re.split(r"(?<=[.!?。다요])\s+|\n+", reply or "")
    claims = any(_CLAIMS.search(x) and _ABOUT.search(x) and not _FAILED.search(x)
                 for x in sentences)
    if not saved:
        if not claims:
            return ""
        kinds = [k for k in _NAMES if k in offered]
        seen = [_state(ctx, k) for k in kinds]
        version = next((s.get("version") for s in seen if s.get("version")), None)
        at = f" v{version}" if version else ""
        named = "·".join(_NAMES[k] for k in kinds)
        return (f"\n\n(확인: 이번 답에서 {named}는 저장되지 않았습니다. 문서는{at} 그대로입니다. "
                "바꾸려면 다시 요청해 주세요.)")
    if not _FAILED.search(reply or "") and claims:
        return ""
    lines = []
    for kind, record in saved:
        changes = "; ".join(str(c) for c in (record.get("changes") or [])[:6])
        lines.append(f"{_NAMES[kind]} v{record.get('version')}" + (f" ({changes})" if changes
                                                                   else ""))
    return "\n\n(확인: 이번 턴에 저장된 판: " + ", ".join(lines) + ")"
