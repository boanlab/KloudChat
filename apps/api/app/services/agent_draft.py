"""「에이전트로 저장」: what a conversation settled, as a draft agent a person reviews.

Nothing is saved here. The draft prefills the agent form; the person edits it and saves.
「이 대화에서 정한 것」 is distilled from the person's own messages only (never the answers,
never an agent's prompt) by the model the conversation ran on, so a strict-local conversation
sends nothing anywhere it did not already go.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: How much of the person's words is read: the latest messages first, within this budget.
_MAX_CHARS = 12_000
_MAX_TOKENS = 600
_MAX_LINES = 8

_INSTRUCTION = (
    "You read only the messages a person sent in a conversation with an assistant. List what "
    "the person settled about HOW the work should be done, so a reusable agent can do it the "
    "same way next time: the reader or audience, the purpose, tone and register, length or "
    "page count, structure or required sections, things to always include or never include, "
    "units and notation, the sources to trust.\n\n"
    "Rules:\n"
    "- Only what the person's own words state or ask for. Do not infer, do not add advice.\n"
    "- Leave out one-off facts of this particular task (names, dates, amounts, file contents, "
    "the topic itself) unless the person said to always use them.\n"
    "- At most {limit} lines. One rule per line, written as an instruction to the agent, in "
    "Korean, 해라체 or 명사형 (예: 「보고 대상은 팀장. 결론을 맨 앞에 둔다.」).\n"
    "- Start each line with \"- \". Output the lines only. If nothing was settled, output "
    "nothing.\n"
)

_LINE = re.compile(r"^\s{0,4}[-*•·]\s{1,3}(.{2,300})$")


def _transcript(messages: list[str]) -> str:
    """The person's messages, latest kept whole first, oldest dropped past the budget."""
    kept: list[str] = []
    used = 0
    for text in reversed(messages):
        text = (text or "").strip()
        if not text:
            continue
        if used + len(text) > _MAX_CHARS:
            text = text[: max(0, _MAX_CHARS - used)]
        if not text:
            break
        kept.append(text)
        used += len(text)
    return "\n\n---\n\n".join(f"[사용자]\n{t}" for t in reversed(kept))


def lines_of(reply: str) -> list[str]:
    out = []
    for raw in (reply or "").splitlines():
        if m := _LINE.match(raw):
            line = m.group(1).strip()
            if line and line not in out:
                out.append(line)
    return out[:_MAX_LINES]


async def distill(model: str, messages: list[str], api_key: str, *, strict_local: bool,
                  masker: Callable[[str], tuple[str, int]] | None = None
                  ) -> tuple[list[str], dict[str, int]]:
    """`(lines, usage)`: what the person settled; no lines on any failure."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    body = _transcript(messages)
    if not body:
        return [], spent
    text = _INSTRUCTION.format(limit=_MAX_LINES) + "\n\n" + body
    redact = False
    if masker is not None:
        text, hits = masker(text)
        redact = bool(hits)
    try:
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}",
                     **({"x-litellm-enable-message-redaction": "true"} if redact else {})},
            timeout=settings.title_timeout_sec * 2,
        ) as client:
            response = await client.post("/v1/chat/completions", json={
                "model": model,
                "messages": [{"role": "user", "content": text}],
                "temperature": 0,
                "max_tokens": _MAX_TOKENS,
                # A strict-local conversation's words go to its own model or nowhere.
                **({"disable_fallbacks": True} if strict_local else {}),
            })
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.info("agent draft not distilled: %s", type(exc).__name__)
        return [], spent
    raw = data.get("usage") or {}
    spent["inputTokens"] = int(raw.get("prompt_tokens") or 0)
    spent["outputTokens"] = int(raw.get("completion_tokens") or 0)
    choices = data.get("choices") or []
    reply = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    if masker is not None and reply:
        reply = masker(reply)[0]
    return lines_of(reply), spent


def prompt_from(lines: list[str]) -> str:
    """The 지시 section a draft starts with."""
    if not lines:
        return ""
    return "이 대화에서 정한 것:\n" + "\n".join(f"- {line}" for line in lines)


__all__ = ["distill", "lines_of", "prompt_from"]
