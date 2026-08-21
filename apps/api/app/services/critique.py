"""One reading of a finished document by somebody who did not write it.

OpenDesign's Critique Theater seats a five-person jury and runs up to three
rounds, refusing to ship under 8.0. That is five to fifteen model calls per
artifact. Here every call is somebody's credit and the bill is shown before the
turn, so the panel is one reviewer and one pass, asked for explicitly.

What it produces is deliberately the **same shape the linter produces** — a
severity, a place, a sentence — so the panel has one list of things to look at
rather than two, and the difference between them is only where they came from.
The linter is free and certain; this costs a call and is an opinion. The score
carries that: it is a reading, not a gate. Nothing is blocked by it.

The rubric comes from the template the document was written into
(`checklist.md`), or from `_DEFAULT_RUBRIC` for the built-in report and deck
tracks. Reviewing rules are kept apart from writing rules on purpose: folded
into the brief, a rubric becomes a checklist the model writes *to* rather than
one it can be measured by.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: Findings beyond this are a rewrite, not a review.
MAX_FINDINGS = 6

#: Enough of the document to judge. A reviewer who has read six pages of a
#: twelve-page report can still say whether the first six hold together.
_MAX_SOURCE = 10_000

#: What a report or a deck is read against when it was not written into a
#: template. Deliberately about the things every document of its kind owes its
#: reader, rather than about how it looks.
_DEFAULT_RUBRIC = """- 첫 부분을 읽고 이 문서가 무엇을 위한 것인지, 누가 읽는지 알 수 있는가.
- 부분끼리 내용이 겹치지 않는가.
- 주장마다 근거가 붙어 있는가. 숫자에 단위와 기준 시점이 있는가.
- 확인하지 못한 것을 확인한 것처럼 쓰지 않았는가.
- 마지막에 읽는 사람이 할 일이 남는가."""

_PROMPT = """너는 이 문서를 쓰지 않은 검토자다. 아래 기준으로 읽고 평가하라.

기준:
{rubric}

규칙:
- score 는 0.0~10.0. 기준을 모두 충족하면 9 이상, 절반이면 5 안팎이다.
  후하게 주지 마라. 근거를 댈 수 없는 점수는 쓸모가 없다.
- findings 는 최대 {limit}개. 고쳐야 할 것만 적고, 잘한 점은 적지 마라.
- severity 는 "P0" 또는 "P1". P0 는 이대로 내보내면 안 되는 것, P1 은 읽기에
  나쁜 것이다.
- where 는 문제가 있는 부분의 제목을 그대로 옮겨라. 문서 전체에 대한 것이면 빈 문자열.
- message 는 한 문장. 무엇이 문제인지와 어떻게 고칠지를 함께 적어라.
  "개선이 필요하다" 처럼 무엇을 하라는지 알 수 없는 말은 쓰지 마라.
- 문서에 없는 내용을 지적하지 마라. 읽은 것에 대해서만 쓴다.

JSON 객체로만 답하라.
예:
{{"score": 6.5,
  "findings": [
    {{"severity": "P0", "where": "예상 효과",
      "message": "42% 절감에 출처가 없다. 근거를 밝히거나 숫자를 빼라."}},
    {{"severity": "P1", "where": "",
      "message": "결론에 담당과 기한이 없어 다음에 무엇이 일어나는지 알 수 없다."}}]}}

문서 제목: {title}

문서:
{body}"""


class CritiqueError(RuntimeError):
    """Nothing usable came back. The message is written for the person who asked."""


async def _complete(model: str, prompt: str, api_key: str) -> tuple[str, dict[str, int]]:
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 900,
            },
        )
        response.raise_for_status()
        payload = response.json()

    raw = payload.get("usage") or {}
    return (payload["choices"][0]["message"]["content"] or "").strip(), {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _scan(body: str) -> tuple[list[str], bool, int]:
    """`(brackets still open, ends inside a string, where the last item ended)`.

    The third is where the text can be cut without leaving half of something
    behind: after a bracket that closed inside a container, or at the comma
    before whatever came next.
    """
    stack: list[str] = []
    in_string = escaped = False
    cut = 0
    for index, char in enumerate(body):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if stack:
                stack.pop()
            if stack:
                cut = index + 1
        elif char == "," and stack:
            cut = index
    return stack, in_string, cut


def _closed(body: str) -> str:
    """The same text with whatever it left open closed off, and nothing added.

    A small model ends its JSON a bracket short often enough that refusing the
    whole review over it throws away the call the reader paid for — a score and
    three findings, lost to one `}`. So the brackets still open are closed, and
    a finding the reply stopped in the middle of is dropped whole rather than
    guessed at: what is stored is only what the model actually finished saying.
    """
    stack, in_string, cut = _scan(body)
    if in_string:
        body = body[:cut]
        stack, _, _ = _scan(body)
    return body.rstrip().rstrip(",") + "".join(reversed(stack))


def _object(text: str) -> dict:
    body = re.sub(r"^\s*```[A-Za-z]*\s*\n(.*?)\n?\s*```\s*$", r"\1", text.strip(), flags=re.S)
    start = body.find("{")
    if start < 0:
        return {}
    body = body[start:]
    end = body.rfind("}")
    if end > 0:
        try:
            return json.loads(body[: end + 1])
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(_closed(body))
    except json.JSONDecodeError:
        return {}


def document(parts: list[dict[str, str]]) -> str:
    """The document as a reviewer reads it: headings and the words under them."""
    lines: list[str] = []
    for part in parts:
        heading = (part.get("heading") or "").strip()
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", part.get("text") or "")).strip()
        if heading:
            lines.append(f"## {heading}")
        if text:
            lines.append(text)
    return "\n".join(lines)


async def review(
    *, title: str, body: str, rubric: str, model: str, api_key: str
) -> tuple[dict, dict[str, int]]:
    """`(critique, usage)` — a score and what to fix, normalised.

    A score outside the scale, a severity that is not one of the two, a finding
    with no message: all dropped here rather than stored. The panel renders
    these beside the linter's own findings, and one made-up row there would
    make the whole list read as decoration.
    """
    if len(body.strip()) < 40:
        raise CritiqueError("검토할 내용이 너무 짧습니다.")

    reply, usage = await _complete(
        model,
        _PROMPT.format(
            rubric=(rubric or _DEFAULT_RUBRIC).strip(),
            limit=MAX_FINDINGS,
            title=title[:200],
            body=body[:_MAX_SOURCE],
        ),
        api_key,
    )
    data = _object(reply)
    if not data:
        # Both ends, because a reply that cannot be read is nearly always
        # well-formed until it stops, and the head alone shows none of that.
        log.warning(
            "critique unparseable (%d chars): %s … %s", len(reply), reply[:200], reply[-200:]
        )
        raise CritiqueError(
            "검토 결과를 읽어내지 못했습니다 — 모델이 답을 끝맺지 못했습니다. 다시 요청해 보세요."
        )

    try:
        score = round(min(10.0, max(0.0, float(data.get("score")))), 1)
    except (TypeError, ValueError):
        raise CritiqueError("검토 결과에 점수가 없습니다 — 다시 요청해 보세요.") from None

    findings = []
    for raw in (data.get("findings") or [])[:MAX_FINDINGS]:
        if not isinstance(raw, dict):
            continue
        message = str(raw.get("message") or "").strip()
        if not message:
            continue
        severity = str(raw.get("severity") or "").upper()
        findings.append(
            {
                "severity": severity if severity in ("P0", "P1") else "P1",
                "rule": "critique",
                "message": message[:300],
                "where": str(raw.get("where") or "").strip()[:120],
            }
        )
    return {"score": score, "findings": findings}, usage


__all__ = ["MAX_FINDINGS", "CritiqueError", "document", "review"]
