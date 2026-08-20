"""LiteLLM chat streaming, translated into KloudChat's SSE event shape.

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
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.core.config import settings
from app.models.chat import Message, Role, TurnFailure
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


#: How much of the opening sentence a provisional title keeps. Long enough to
#: tell two requests apart in a sidebar, short enough that the row does not
#: have to truncate it a second time.
TITLE_CHARS = 40


def provisional_title(prompt: str) -> str:
    """The name a session carries before anything better exists.

    Chat, report and deck sessions overwrite this with `generate_title`'s
    output once the turn has both halves to summarise. A picture, a clip or a
    narration never gets that far — there is no reply to summarise, and the
    prompt already *is* the sentence the person wrote — so on those surfaces
    this is the final name. One rule rather than two, because two would drift
    and the sidebar would start naming the same request differently depending
    on which screen made it.
    """
    return " ".join((prompt or "").split())[:TITLE_CHARS]


def media_prompt(session_id: str, prompt: str, *, unanswered: bool = False) -> Message:
    """The person's own sentence, on a surface whose reply is not a sentence.

    Stored for the same reason it is stored everywhere else: it is the half of
    the conversation somebody wrote themselves, and a screen that swallows it
    is a screen that lost what they asked for. That it was once left out here
    is the whole of why these conversations opened blank.

    `unanswered` marks the request that came back with nothing — the model
    refused, the gateway was down — exactly as a chat turn that dies before its
    first word marks the question rather than inventing a reply to carry the
    bad news.
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
    """What came back, as the thing itself rather than a sentence about it.

    The content is empty on purpose and must stay empty. A picture is not a
    sentence, and prose written here — "이미지를 만들었습니다" — would be the
    model quoted saying something no model said. The ids are the answer; the
    transcript renders them where an answer goes.

    `partial` is the batch that broke in the middle: three of four pictures
    arrived and the fourth call failed. What arrived is kept and said to be
    less than what was asked for, which is the same thing a half-written chat
    answer does with `interrupted`.
    """
    return Message(
        session_id=session_id,
        role=Role.assistant,
        content="",
        artifact_ids=list(artifact_ids),
        model=model or None,
        # Only the charge. There are no tokens worth printing under a picture,
        # and the figure a reader wants beside one they paid for is what it
        # cost.
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
        # Without this the final chunk carries no usage and the turn cannot be
        # billed on real numbers.
        "stream_options": {"include_usage": True},
        # Redundant with the virtual key, and cheap: it also tags the spend row
        # when a call has fallen back to the master key.
        "user": user_id,
    }
    if strict_local:
        payload["disable_fallbacks"] = True

    usage: dict[str, int] | None = None
    reported_model: str | None = None
    # Tool call fragments arrive spread across chunks, keyed by index.
    open_steps: dict[int, dict[str, Any]] = {}

    try:
        # Base URL from the settings store, not the environment — an administrator
        # who repoints the proxy expects chat to follow.
        base, _ = await settings_store.litellm_config()
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                **(
                    {"x-litellm-enable-message-redaction": "true"}
                    if redact_logging
                    else {}
                ),
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
    """`(title, usage)` from one short non-streaming call.

    Best effort — a session with no title is a cosmetic problem, and blocking
    the turn on it would not be. The tokens are reported either way: nobody
    asks for a title, so the call that writes one has to be visible in the
    ledger rather than absorbed.
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
                **(
                    {"x-litellm-enable-message-redaction": "true"}
                    if redact_logging
                    else {}
                ),
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
                    **(
                        {"disable_fallbacks": True}
                        if strict_local or disable_fallbacks
                        else {}
                    ),
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
    # The tokens go back even when the reply was unusable: they were spent.
    return title[:80] or None, spent
