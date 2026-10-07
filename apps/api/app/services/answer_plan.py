"""A short plan of what an expert answer holds, added before a substantial chat answer.

One quick call on the same model and already-masked messages; a failed plan changes nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("kchat")

#: A request to make something, plan something or act on something.
_SUBSTANTIAL = re.compile(
    r"짜\s{0,2}줘|만들어|정리해|써\s{0,2}줘|작성|계획|설계|추천|비교|어떻게\s{0,2}(?:해야|하면|하나)|"
    r"방법|해야\s{0,2}(?:해|하나|할까)|대응|전략|일정|예산|문항|지도안|안내문|기획|제안|"
    r"검토|분석|진단|돌려\s{0,2}줘|알려\s{0,2}줘"
)
#: Short exchanges that are not worth a plan: thanks, yes/no, a one-word follow-up.
_CASUAL = re.compile(r"^(?:고마워|감사|ㅇㅋ|오케이|응|네|아니|좋아|그래)\b")

PROMPT = """(이 메시지는 답을 쓰기 전의 준비다. 답은 쓰지 마라.)
위 마지막 요청에 그 분야 전문가가 완전한 답을 쓴다면 무엇을 담을지 항목 6~8개로 적어라.
요청이 직접 요구한 부분을 먼저, 그다음 전문가라면 덧붙일 것 — 실무 세부(수치·시간·기준·예시),
가장 흔하거나 비싼 실수, 대안, 확인하거나 도움받을 곳, 앞선 대화에서 사용자가 말한 사정과의 연결.
마지막 항목은 「정확히: 」로 시작해, 이 답에서 틀리기 쉬운 개념·용어 구분 한두 가지를
바르게 적는다(예: 이온 결합 물질은 분자가 아니므로 분자식이 아니라 화학식으로 쓴다).
각 항목은 「- 」로 시작하는 짧은 구 하나(30자 안팎)다 — 설명 문장을 쓰지 마라.
사실이나 수치를 새로 지어내지 마라."""


def wanted(content: str) -> bool:
    text = (content or "").strip()
    return len(text) >= 25 and not _CASUAL.match(text) and bool(_SUBSTANTIAL.search(text))


async def plan(messages: list[dict[str, Any]], *, complete, model: str, api_key: str) -> str:
    """The plan's bullet lines, or `""`."""
    probe = [*messages, {"role": "user", "content": PROMPT}]
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
    """`messages` with the plan beside the latest question (text questions only)."""
    if not outline or not messages or messages[-1].get("role") != "user":
        return messages
    last = messages[-1]
    if not isinstance(last.get("content"), str):
        return messages
    note = (
        "\n\n(답을 쓰기 전에 정리한 답의 뼈대다. 이 항목을 빠짐없이, 실무자가 바로 쓸 수 있는 "
        "깊이로 다뤄라. 뼈대 자체를 보여 주거나 언급하지는 마라.)\n" + outline
    )
    return [*messages[:-1], {**last, "content": last["content"] + note}]
