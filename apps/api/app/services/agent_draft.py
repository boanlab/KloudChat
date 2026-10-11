"""「에이전트로 저장」 draft from the person's own messages, via the conversation's model."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

#: budget for the person's words, latest first
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
    """Person's messages, latest whole first, oldest dropped past budget."""
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


class DistillFailed(Exception):
    """Model unanswered after retry: draft's 지시 = 「not read」, not 「nothing settled」."""


#: first try + one retry on timeout/connection/5xx (never 4xx)
_TRIES = 2


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def distill(model: str, messages: list[str], api_key: str, *, strict_local: bool,
                  masker: Callable[[str], tuple[str, int]] | None = None
                  ) -> tuple[list[str], dict[str, int]]:
    """`(lines, usage)`; raises `DistillFailed` when unreadable, never a fake empty answer."""
    spent = {"inputTokens": 0, "outputTokens": 0}
    body = _transcript(messages)
    if not body:
        return [], spent
    text = _INSTRUCTION.format(limit=_MAX_LINES) + "\n\n" + body
    redact = False
    if masker is not None:
        text, hits = masker(text)
        redact = bool(hits)
    data: dict = {}
    for attempt in range(_TRIES):
        try:
            base, _ = await settings_store.litellm_config()
            async with httpx.AsyncClient(
                base_url=base.rstrip("/"),
                headers={"Authorization": f"Bearer {api_key}",
                         **({"x-litellm-enable-message-redaction": "true"} if redact else {})},
                timeout=settings.agent_draft_timeout_sec,
            ) as client:
                response = await client.post("/v1/chat/completions", json={
                    "model": model,
                    "messages": [{"role": "user", "content": text}],
                    "temperature": 0,
                    "max_tokens": _MAX_TOKENS,
                    # strict-local: own model or nowhere
                    **({"disable_fallbacks": True} if strict_local else {}),
                })
                response.raise_for_status()
                data = response.json()
            break
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.info("agent draft not distilled (try %d): %s", attempt + 1,
                     type(exc).__name__)
            if attempt + 1 >= _TRIES or not _retryable(exc):
                raise DistillFailed(type(exc).__name__) from exc
    raw = data.get("usage") or {}
    spent["inputTokens"] = int(raw.get("prompt_tokens") or 0)
    spent["outputTokens"] = int(raw.get("completion_tokens") or 0)
    choices = data.get("choices") or []
    reply = str(((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    if masker is not None and reply:
        reply = masker(reply)[0]
    return lines_of(reply), spent


#: line about this conversation's material, not a future rule
_THIS_ONE = re.compile(
    r"(?:아래|위|이번|이|첨부(?:한|된)?|붙인|올린|보낸)\s{0,2}(?:의\s{0,2})?"
    r"(?:회의\s{0,2})?(?:메모|자료|파일|대화|메일|내용|녹음|전사본?|문서|표|데이터)")
#: date/day stamps in a title
_DATED = re.compile(
    r"\d{2,4}\s{0,2}[./-]\s{0,2}\d{1,2}(?:\s{0,2}[./-]\s{0,2}\d{1,2})?\.?"
    r"|\d{1,4}\s{0,2}(?:년|월|일|주차?)|\([월화수목금토일]\)|(?<![가-힣])[1-4]분기")


#: 「없음」-style lines or echoed header
_NOTHING = re.compile(
    r"^[\s()\[\]「」.·:-]{0,6}(?:(?:해당\s{0,2}(?:사항\s{0,2})?)?없음|없습니다|정한\s{0,2}것\s{0,2}없음"
    r"|이\s{0,2}대화에서\s{0,2}정한\s{0,2}것|none|n/?a)[\s()\[\]「」.·:-]{0,6}$", re.I)
#: at least two letters or digits
_WORDY = re.compile(r"[가-힣A-Za-z0-9]{2}")


def keep(line: str) -> bool:
    """Whether a distilled line is a future rule (not material-specific, empty, or 「없음」)."""
    text = (line or "").strip()
    return bool(_WORDY.search(text)) and not _NOTHING.match(text) \
        and not _THIS_ONE.search(text)


def name_for(title: str, template_name: str = "") -> str:
    """Draft name: template name, else title without dates."""
    if template_name:
        return f"{template_name} 에이전트"
    stem = " ".join(_DATED.sub(" ", title or "").split()).strip(" -·:,")
    return f"{stem[:30]} 에이전트" if len(stem) >= 2 else "새 에이전트"


def prompt_from(lines: list[str]) -> str:
    """Draft's starting 지시 section."""
    lines = [line.strip() for line in lines if keep(line)]
    if not lines:
        return ""
    return "이 대화에서 정한 것:\n" + "\n".join(f"- {line}" for line in lines)


__all__ = ["DistillFailed", "distill", "keep", "lines_of", "name_for", "prompt_from"]
