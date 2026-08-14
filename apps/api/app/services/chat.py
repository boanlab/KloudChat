"""LiteLLM chat streaming, translated into kchat's SSE event shape.

The upstream is OpenAI-compatible, so this module's whole job is:

* forward the assembled conversation with `stream=True`
* turn content deltas into `delta` events
* turn tool calls into `step` events the UI can show inline while they run
* capture the final usage block so the turn can be settled against credits

Tool routing needs no special case here: the model names the tool, the agent loop
runs it, and this module only reports what happened.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)


class ChatStreamError(RuntimeError):
    pass


# Tool name → what the user should see while it runs. Anything unlisted falls
# back to the raw name, which is ugly but honest.
_STEP_LABELS: dict[str, str] = {
    "web_search": "웹 검색 중",
    "search": "웹 검색 중",
    "fetch_url": "문서 읽는 중",
    "fetch": "문서 읽는 중",
    "execute_code": "코드 실행 중",
    "deep_research": "심층 조사 중",
    "get_current_time": "현재 시각 확인 중",
    "my_usage": "사용량 조회 중",
}


def step_label(tool_name: str) -> str:
    base = tool_name.split("__")[-1]
    return _STEP_LABELS.get(base, base.replace("_", " "))


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_completion(
    model: str,
    messages: list[dict[str, Any]],
    user_id: str,
    api_key: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yields kchat stream events. The caller owns persistence and billing.

    Emits, in order: any number of `step`/`delta`, then exactly one `usage`
    (possibly with zeroed counts when the upstream withheld it), then nothing.
    `done` is the caller's to send, after it has settled credits.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        # Without this the final chunk carries no usage and the turn cannot be
        # billed on real numbers.
        "stream_options": {"include_usage": True},
        # Redundant with the virtual key, and cheap: it also tags the spend row
        # when a call has fallen back to the master key.
        "user": user_id,
    }

    usage: dict[str, int] | None = None
    # Tool call fragments arrive spread across chunks, keyed by index.
    open_steps: dict[int, dict[str, Any]] = {}

    try:
        # Base URL from the settings store, not the environment — an administrator
        # who repoints the proxy expects chat to follow.
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
        ) as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:400]
                    raise ChatStreamError(f"upstream_{response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        log.warning("undecodable chunk from litellm: %r", raw[:200])
                        continue

                    if chunk.get("usage"):
                        u = chunk["usage"]
                        usage = {
                            "inputTokens": int(u.get("prompt_tokens") or 0),
                            "outputTokens": int(u.get("completion_tokens") or 0),
                        }

                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}

                        for call in delta.get("tool_calls") or []:
                            index = call.get("index", 0)
                            step = open_steps.get(index)
                            name = (call.get("function") or {}).get("name")
                            if step is None and name:
                                step = {"id": f"step{index}", "label": step_label(name)}
                                open_steps[index] = step
                                yield {
                                    "type": "step",
                                    "id": step["id"],
                                    "label": step["label"],
                                    "status": "running",
                                }

                        text = delta.get("content")
                        if text:
                            # The first visible token means every tool call that
                            # was open has produced its answer.
                            for step in open_steps.values():
                                if not step.get("closed"):
                                    step["closed"] = True
                                    yield {
                                        "type": "step",
                                        "id": step["id"],
                                        "label": step["label"],
                                        "status": "done",
                                    }
                            yield {"type": "delta", "text": text}

    except httpx.HTTPError as exc:
        raise ChatStreamError(f"upstream_unreachable: {exc}") from exc

    for step in open_steps.values():
        if not step.get("closed"):
            yield {"type": "step", "id": step["id"], "label": step["label"], "status": "done"}

    yield {"type": "usage", **(usage or {"inputTokens": 0, "outputTokens": 0})}


async def generate_title(
    model: str, first_user_message: str, first_reply: str, api_key: str
) -> str | None:
    """One short non-streaming call. Best effort — a session with no title is a
    cosmetic problem, and blocking the turn on it would not be.
    """
    prompt = (
        "다음 대화의 핵심 주제를 나타내는 제목을 작성하세요. "
        "반드시 한국어로, 5단어 이내, 따옴표나 문장부호 없이 제목 텍스트만 출력하세요.\n\n"
        f"사용자: {first_user_message[:1500]}\n"
        f"어시스턴트: {first_reply[:1500]}"
    )
    try:
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.title_timeout_sec,
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 40,
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.info("title generation skipped: %s", exc)
        return None

    choices = data.get("choices") or []
    if not choices:
        return None
    title = (choices[0].get("message") or {}).get("content") or ""
    title = title.strip().strip("\"'").splitlines()[0].strip() if title.strip() else ""
    return title[:80] or None
