"""Minutes tell the meeting that was held in the past tense.

「영업1팀 주간회의가 열립니다」, 「결론에 이르지 못합니다」, 「참석자 전원이 합의합니다」 read as a
meeting still to come. A sentence ending in one of the meeting's own acts (held, discussed,
agreed, decided, attended) is put in the past by code; the next meeting, follow-ups, tables,
quotes and any sentence looking ahead (「다음 회의에서 결정합니다」) stay as written.
"""

from __future__ import annotations

import re

from app.services import units

#: acts of the meeting itself, as 「…합니다/됩니다」
_ACTS = ("개최|논의|토의|토론|심의|협의|합의|결정|확정|의결|진행|참석|불참|제시|발표|동의|반대|"
         "지적|질의|이월|보류")
_SINO = re.compile(rf"(?<![가-힣])({_ACTS})(합니다|됩니다)$")
#: …a verb of its own, and 「…하기로 합니다」 (agreed to)
_NATIVE = {"열립니다": "열렸습니다", "정해집니다": "정해졌습니다", "다룹니다": "다뤘습니다",
           "모입니다": "모였습니다", "마칩니다": "마쳤습니다", "끝납니다": "끝났습니다",
           "나옵니다": "나왔습니다", "밝힙니다": "밝혔습니다", "못합니다": "못했습니다"}
_NATIVE_END = re.compile(rf"(?<![가-힣])({'|'.join(_NATIVE)})$")
_AGREED = re.compile(r"(?<=기로 )(합니다|됩니다)$")
#: a sentence looking ahead
_AHEAD = re.compile(r"다음\s?(?:회의|미팅|모임|주|달|번)|차기|향후|앞으로|예정|이후|이전까지|"
                    r"뒤에|후에|후부터")
#: parts about what comes next
_AHEAD_PART = re.compile(r"다음|차기|향후|후속|실행|액션|할\s?일|조치|계획|일정|과제|action|next",
                         re.I)
#: what may follow the verb: a mark 「(확인 필요)」, the full stop
_TAIL = re.compile(r"(\s{0,2}(?:\([^()\n]{0,40}\)\s{0,2})?[.!?。]?)$")
_BREAK = re.compile(f"({units.SENTENCE_BREAK.pattern}|(?<=니다[.!?])(?=[가-힣]))")
_QUOTED = re.compile(r"「[^」\n]{0,200}」|\"[^\"\n]{0,200}\"")


def _past(sentence: str) -> str:
    tail = _TAIL.search(sentence)
    end = tail.start() if tail else len(sentence)
    body, rest = sentence[:end], sentence[end:]
    # 「…하기로 합니다」 was agreed at this meeting, whatever it looks ahead to
    if m := _AGREED.search(body):
        return body[:m.start()] + ("했습니다" if m.group(1) == "합니다" else "되었습니다") + rest
    if _AHEAD.search(_QUOTED.sub("", body)):
        return sentence
    if m := _SINO.search(body):
        return body[:m.start(2)] + ("했습니다" if m.group(2) == "합니다" else "되었습니다") + rest
    if m := _NATIVE_END.search(body):
        return body[:m.start()] + _NATIVE[m.group(1)] + rest
    return sentence


def past(content: str) -> str:
    """Prose lines of a section with each sentence ending in a meeting's act put in the past."""
    lines = []
    for line in (content or "").split("\n"):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(("|", "#", "```", ">", "![", "<")):
            lines.append(line)
            continue
        # sentences and the breaks between them, a glued 「…합니다.이번」 one too
        parts = _BREAK.split(line)
        lines.append("".join(p if k % 2 else _past(p) for k, p in enumerate(parts)))
    return "\n".join(lines)


def held(sections: list[dict], skip: re.Pattern | None = None) -> tuple[list[dict], int]:
    """Sections telling the meeting held, put in the past; parts about what comes next (and
    those `skip` names) stay. Returns the sections and how many sentences changed."""
    out, changed = [], 0
    for sec in sections:
        heading = str(sec.get("heading") or "")
        content = sec.get("content")
        if not isinstance(content, str) or _AHEAD_PART.search(heading) or (
                skip is not None and skip.search(heading)):
            out.append(sec)
            continue
        mended = past(content)
        if mended != content:
            changed += sum(1 for a, b in zip(content.split("\n"), mended.split("\n"),
                                              strict=True) if a != b)
            sec = {**sec, "content": mended}
        out.append(sec)
    return out, changed


__all__ = ["held", "past"]
