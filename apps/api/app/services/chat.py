"""LiteLLM chat streaming translated into KloudChat SSE events, plus titles and media-turn messages.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.core.config import settings
from app.models.chat import Message, Role, TurnFailure
from app.services import settings_store

log = logging.getLogger(__name__)


class ChatStreamError(RuntimeError):
    pass


# Tool name → progress label shown while it runs.
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


# Tool name → noun form shown on a finished step.
_STEP_TITLES: dict[str, str] = {
    "web_search": "웹 검색",
    "search": "웹 검색",
    "fetch_url": "문서 읽기",
    "fetch": "문서 읽기",
    "execute_code": "코드 실행",
    "deep_research": "심층 조사",
    "get_current_time": "현재 시각",
    "my_usage": "사용량 조회",
}


def step_title(tool_name: str) -> str:
    base = tool_name.split("__")[-1]
    return _STEP_TITLES.get(base, base.replace("_", " "))


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


TITLE_CHARS = 40


def provisional_title(prompt: str) -> str:
    """Session title from the prompt's first `TITLE_CHARS`; final on media surfaces, replaced by
    `generate_title` elsewhere.
    """
    return " ".join((prompt or "").split())[:TITLE_CHARS]


def media_prompt(session_id: str, prompt: str, *, unanswered: bool = False) -> Message:
    """The user turn of a media (image/audio/video) session; `unanswered` marks a request that
    produced nothing.
    """
    return Message(
        session_id=session_id,
        role=Role.user,
        content=prompt,
        failure=TurnFailure.no_answer if unanswered else None,
    )


def media_answer(
    session_id: str,
    artifact_ids: list[str],
    *,
    model: str = "",
    credits: int = 0,
    partial: bool = False,
) -> Message:
    """The assistant turn of a media session: artifact ids, no prose.

    `content` must stay empty; the transcript renders the artifacts. `partial`
    marks a batch that failed midway and is stored as `interrupted`.
    """
    return Message(
        session_id=session_id,
        role=Role.assistant,
        content="",
        artifact_ids=list(artifact_ids),
        model=model or None,
        usage={"credits": credits},
        failure=TurnFailure.interrupted if partial else None,
    )


async def stream_completion(
    model: str,
    messages: list[dict[str, Any]],
    user_id: str,
    api_key: str,
    *,
    strict_local: bool = False,
    redact_logging: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Yields KloudChat stream events. The caller owns persistence and billing.

    Emits, in order: any number of `step`/`delta`, then exactly one `usage`
    (possibly with zeroed counts when the upstream withheld it), then nothing.
    `done` is the caller's to send, after it has settled credits.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},  # else the final chunk carries no usage
        "user": user_id,  # tags the spend row even when a call fell back to the master key
    }
    if strict_local:
        payload["disable_fallbacks"] = True

    usage: dict[str, int] | None = None
    reported_model: str | None = None
    # Tool call fragments arrive across chunks, keyed by index.
    open_steps: dict[int, dict[str, Any]] = {}

    try:
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                **({"x-litellm-enable-message-redaction": "true"} if redact_logging else {}),
            },
            timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
        ) as client:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:400]
                    detail = "response redacted" if redact_logging else body
                    raise ChatStreamError(f"upstream_{response.status_code}: {detail}")

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

                    actual_model = chunk.get("model")
                    if (
                        isinstance(actual_model, str)
                        and actual_model
                        and actual_model != reported_model
                    ):
                        reported_model = actual_model
                        yield {
                            "type": "model_route",
                            "routedModel": model,
                            "actualModel": actual_model,
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
                            # First visible token: every open tool call has answered.
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
    model: str,
    first_user_message: str,
    first_reply: str,
    api_key: str,
    *,
    masker: Callable[[str], tuple[str, int]] | None = None,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
) -> tuple[str | None, dict[str, int]]:
    """`(title, usage)` from one short non-streaming call; title is None on failure, usage is
    reported either way.
    """
    spent = {"inputTokens": 0, "outputTokens": 0}
    if masker is not None:
        first_user_message, user_hits = masker(first_user_message)
        first_reply, reply_hits = masker(first_reply)
        redact_logging = redact_logging or bool(user_hits + reply_hits)

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
            headers={
                "Authorization": f"Bearer {api_key}",
                **({"x-litellm-enable-message-redaction": "true"} if redact_logging else {}),
            },
            timeout=settings.title_timeout_sec,
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 40,
                    **({"disable_fallbacks": True} if strict_local or disable_fallbacks else {}),
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.info("title generation skipped: %s", exc)
        return None, spent

    raw = data.get("usage") or {}
    spent = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    choices = data.get("choices") or []
    if not choices:
        return None, spent
    title = (choices[0].get("message") or {}).get("content") or ""
    title = title.strip().strip("\"'").splitlines()[0].strip() if title.strip() else ""
    if masker is not None:
        title = masker(title)[0]
    return title[:80] or None, spent
