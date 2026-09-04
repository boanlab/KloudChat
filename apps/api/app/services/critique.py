"""One-pass model review of a finished document: a score and findings in the linter's shape.

The rubric is the template's `checklist.md`, or `_DEFAULT_RUBRIC` for the
built-in report and deck tracks. The score is advisory; nothing is gated on it.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.core.config import settings
from app.services import arithmetic, settings_store

log = logging.getLogger(__name__)

MAX_FINDINGS = 6

#: Characters of the document sent to the reviewer.
_MAX_SOURCE = 10_000

#: Rubric for documents not written into a template.
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
- **산수는 지적하지 마라.** 문서의 식은 따로 기계가 검산한다. 「차액 계산이 맞지
  않는다」「합산 값이 다르다」 같은 지적은 네가 틀리는 자리다. 식이 없는 주장에
  근거가 없다는 지적은 해도 된다.

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


#: A finding claiming the document's sums are wrong; dropped when
#: `arithmetic.findings` disagrees.
_ARITHMETIC_CLAIM = re.compile(
    r"(계산|합산|차액|곱|산출)[^.]{0,40}(일치하지|맞지 않|다르|틀리|오류)"
)


class CritiqueError(RuntimeError):
    """Nothing usable came back; the message is user-facing."""


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
    """`(closers still open, ends inside a string, offset of the last complete item)`."""
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
    """Truncated JSON with open brackets closed; a half-written item is dropped whole."""
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
    """`(critique, usage)`: score clamped to 0..10, findings normalised to the linter's shape."""
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
        # Both ends: an unreadable reply is usually well-formed until it stops.
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
    # Arithmetic findings are kept only when the checker also found an error.
    sums_wrong = bool(arithmetic.findings(body))
    for raw in (data.get("findings") or [])[:MAX_FINDINGS]:
        if not isinstance(raw, dict):
            continue
        message = str(raw.get("message") or "").strip()
        if not message:
            continue
        if not sums_wrong and _ARITHMETIC_CLAIM.search(message):
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
