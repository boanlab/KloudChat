"""Short expert-answer plan prepended to substantial chat answers; failure is a no-op."""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("kchat")

#: make/plan/act requests
_SUBSTANTIAL = re.compile(
    r"짜\s{0,2}줘|만들어|정리해|써\s{0,2}줘|작성|계획|설계|추천|비교|어떻게\s{0,2}(?:해야|하면|하나)|"
    r"방법|해야\s{0,2}(?:해|하나|할까)|대응|전략|일정|예산|문항|지도안|안내문|기획|제안|"
    r"검토|분석|진단|돌려\s{0,2}줘|알려\s{0,2}줘"
)
#: short exchanges not worth a plan
_CASUAL = re.compile(r"^(?:고마워|감사|ㅇㅋ|오케이|응|네|아니|좋아|그래)\b")
#: the user fixed the answer's form, scope or length; an expert skeleton would fight it
#: (「진단과 치료 계획만 답하고 다른 건 쓰지 마. 설명도 쓰지 마.」)
_RESTRICTED = re.compile(
    r"만\s{0,2}(?:답해|답하|써|적어|알려|말해|보여|줘|주세요|원해)|"
    r"(?:쓰지|붙이지|넣지|달지|덧붙이지|설명하지)\s{0,2}마|"
    r"설명\s{0,2}(?:은|도|없이|빼|생략|필요\s{0,2}없)|"
    r"다른\s{0,2}(?:건|것은|말은)\s{0,2}(?:빼|필요\s{0,2}없|쓰지)|"
    r"\d+\s{0,2}(?:자|단어|줄|문장|단락|문단)\s{0,2}(?:이내|이하|안팎|내외|로|으로|만)|"
    r"(?:한|두|세)\s{0,2}(?:줄|문장|단락|문단)\s{0,2}(?:로|으로|만|이내)|"
    r"(?:표|목록)\s{0,2}(?:로만|만으로)|"
    # 「- 길이: 짧게」, 「짧게 써 줘」: the length is set (r8 QMX P38: a plan grew the mail)
    r"길이\s{0,2}[:：]\s{0,2}(?:짧|간단|간결)|(?:짧고|짧게|간단히|간결하게)\s{0,2}(?:써|적어|작성해)|"
    r"(?:다음|아래)\s{0,2}(?:형식|양식|순서|구성|틀|절|항목)\s{0,2}(?:으로|대로|에\s{0,2}맞|을\s{0,2}따|로)|"
    r"\b(?:answer only|only (?:the|a|one)|don'?t explain|no explanations?|in \d+ words|"
    r"one (?:line|sentence))\b",
    re.IGNORECASE,
)

PROMPT = """(이 메시지는 답을 쓰기 전의 준비다. 답은 쓰지 마라.)
List 4-7 points an expert would cover in a good answer to the last request above.
Put what the request directly asks for first. Then add only what this case really needs:
practical detail (figures, timing, criteria, examples) the request or the material gives or
that general knowledge settles{extras}.
Do not add a point just to fill a category; fewer specific points beat generic ones.
A message written for the person (an email, a letter, a notice, an apology, a reply) carries
only the person's own facts: its points name what the message must say of them and its tone,
never a cause, a remedy, a date, a time, a name or a promise the person did not give
(예: 지연 사유·대체 공급·우선 처리를 지어내지 않는다).
The last point starts with 「정확히: 」 and names one or two concepts or terms that are easy
to get wrong in this answer (예: 이온 결합 물질은 분자가 아니므로 분자식이 아니라 화학식으로 쓴다).
Each point is one short phrase (about 30 characters) starting with 「- 」, no sentences.
Do not invent facts or figures."""
#: optional categories; the user's earlier words exist only on a follow-up turn
_EXTRAS = "; a costly mistake, an alternative or where to get help only when they apply"
_EXTRAS_FOLLOW_UP = _EXTRAS + "; how it ties to what the user said earlier in this conversation"


def restricted(content: str) -> bool:
    """Whether the user fixed the answer's form, scope or length (no plan then)."""
    return bool(_RESTRICTED.search(content or ""))


def wanted(content: str) -> bool:
    text = (content or "").strip()
    return (
        len(text) >= 25
        and not _CASUAL.match(text)
        and bool(_SUBSTANTIAL.search(text))
        and not restricted(text)
    )


def _user_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [
        m["content"] for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]


async def plan(messages: list[dict[str, Any]], *, complete, model: str, api_key: str) -> str:
    """Plan bullet lines, or ""."""
    users = _user_texts(messages)
    # a form set in an earlier turn (role-play rules) still holds on the follow-ups
    if any(restricted(text) for text in users):
        return ""
    # first turn: a "tie to what the user said earlier" point invents a past (「앞선 대화에서…」)
    extras = _EXTRAS_FOLLOW_UP if len(users) > 1 else _EXTRAS
    probe = [*messages, {"role": "user", "content": PROMPT.format(extras=extras)}]
    try:
        text, _ = await complete(model, probe, api_key, max_tokens=250)
    except Exception as exc:  # noqa: BLE001 — a failed plan leaves the answer as it was
        log.info("answer plan failed: %s", exc)
        return ""
    lines = [ln.strip() for ln in (text or "").splitlines() if re.match(r"^\s*[-*•]\s+\S", ln)]
    if len(lines) < 3:
        return ""
    return "\n".join("- " + re.sub(r"^[-*•]\s+", "", ln) for ln in lines[:10])


def attach(messages: list[dict[str, Any]], outline: str) -> list[dict[str, Any]]:
    """Messages with the plan beside the latest question (text only)."""
    if not outline or not messages or messages[-1].get("role") != "user":
        return messages
    last = messages[-1]
    if not isinstance(last.get("content"), str):
        return messages
    # optional notes, not a checklist: "cover every point" grew sections nobody asked for
    note = (
        "\n\n(Writer's own notes, not part of the user's request. Use the points that fit "
        "this request, at a depth a practitioner can act on, and silently drop the rest. "
        "The user's wording, requested form and any material they gave come first. "
        "Add no section only because a point names it, and never mention these notes.)\n"
        + outline
    )
    return [*messages[:-1], {**last, "content": last["content"] + note}]
