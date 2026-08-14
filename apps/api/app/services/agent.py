"""The tool-calling loop.

One turn can take several round trips: the model asks for a tool, the loop runs
it, hands back the result and asks again. It ends on prose instead of a call, or
at `max_hops`.

* **Tool results are data, never instructions.** Results are wrapped as `tool`
  role messages, which is what keeps them out of the instruction position; the
  system prompt says the same thing.
* **A failing tool does not fail the turn.** The error text goes back as the
  result, so the model can say what went wrong or try another route.
* **Usage accumulates across hops.** Billing is per turn: a five-hop turn costs
  five prompts' worth of input tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store
from app.services.chat import ChatStreamError, step_label
from app.services.tools.base import Tool, ToolContext, ToolResult, to_openai

log = logging.getLogger(__name__)


async def _client(api_key: str) -> httpx.AsyncClient:
    """Built per turn, so an administrator can repoint the proxy live.

    `api_key` is the caller's virtual key, so the proxy attributes and limits the
    turn against that person.
    """
    base, _ = await settings_store.litellm_config()
    return httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    )


class _Accumulator:
    """Reassembles one streamed choice.

    Tool calls arrive as fragments keyed by index: the name in one chunk, the
    arguments spread across many.
    """

    def __init__(self) -> None:
        self.content: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.usage = {"inputTokens": 0, "outputTokens": 0}
        self.finish_reason: str | None = None

    def add_chunk(self, chunk: dict[str, Any]) -> str | None:
        """Returns newly emitted visible text, if any."""
        if chunk.get("usage"):
            u = chunk["usage"]
            # `+=`: every hop bills its own prompt.
            self.usage["inputTokens"] += int(u.get("prompt_tokens") or 0)
            self.usage["outputTokens"] += int(u.get("completion_tokens") or 0)

        text: str | None = None
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or {}

            for raw in delta.get("tool_calls") or []:
                index = raw.get("index", 0)
                call = self.calls.setdefault(
                    index, {"id": None, "name": "", "arguments": ""}
                )
                if raw.get("id"):
                    call["id"] = raw["id"]
                fn = raw.get("function") or {}
                if fn.get("name"):
                    call["name"] = fn["name"]
                if fn.get("arguments"):
                    call["arguments"] += fn["arguments"]

            piece = delta.get("content")
            if piece:
                self.content.append(piece)
                text = piece
        return text

    def assistant_message(self) -> dict[str, Any]:
        """The turn so far, in the shape the next request expects it back."""
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content) or None}
        if self.calls:
            message["tool_calls"] = [
                {
                    "id": c["id"] or f"call_{i}",
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                }
                for i, c in sorted(self.calls.items())
            ]
        return message


async def _stream_once(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    user_id: str,
    api_key: str,
) -> AsyncIterator[tuple[str, Any]]:
    """Yields `('delta', text)` while streaming, then `('done', _Accumulator)`."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "user": user_id,
    }
    if tools:
        payload["tools"] = to_openai(tools)
        payload["tool_choice"] = "auto"

    acc = _Accumulator()
    try:
        async with await _client(api_key) as client:
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
                    text = acc.add_chunk(chunk)
                    if text:
                        yield "delta", text
    except httpx.HTTPError as exc:
        raise ChatStreamError(f"upstream_unreachable: {exc}") from exc

    yield "done", acc


async def _run_tool(tool: Tool, arguments: str, ctx: ToolContext) -> ToolResult:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        # Malformed JSON from the model. Reported back rather than fatal — it
        # usually retries with valid arguments.
        return ToolResult(
            content=f"오류: 인자를 JSON으로 해석할 수 없습니다: {arguments[:200]}", failed=True
        )
    if not isinstance(parsed, dict):
        return ToolResult(content="오류: 인자는 객체여야 합니다.", failed=True)

    try:
        async with asyncio.timeout(settings.tool_timeout_sec):
            output = await (tool.run(parsed, ctx) if tool.wants_context else tool.run(parsed))
    except TimeoutError:
        return ToolResult(
            content=f"오류: {tool.name} 도구가 시간 안에 응답하지 않았습니다.", failed=True
        )
    except Exception as exc:  # noqa: BLE001 — a broken tool must not end the turn
        log.warning("tool %s failed: %s", tool.name, exc, exc_info=True)
        return ToolResult(content=f"오류: {tool.name} 실행에 실패했습니다 ({exc}).", failed=True)

    if isinstance(output, ToolResult):
        return output
    return ToolResult(content=str(output))


async def run_turn(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    ctx: ToolContext,
) -> AsyncIterator[dict[str, Any]]:
    """Drives one assistant turn to a final answer.

    Emits `step` (running/done/error), `delta`, and exactly one `usage`. `done`
    belongs to the caller, after credits settle.
    """
    by_name = {t.name: t for t in tools}
    conversation = list(messages)
    usage = {"inputTokens": 0, "outputTokens": 0}
    hop = 0

    while True:
        acc: _Accumulator | None = None
        async for kind, value in _stream_once(model, conversation, tools, ctx.user_id, ctx.api_key):
            if kind == "delta":
                yield {"type": "delta", "text": value}
            else:
                acc = value
        assert acc is not None

        usage["inputTokens"] += acc.usage["inputTokens"]
        usage["outputTokens"] += acc.usage["outputTokens"]

        if not acc.calls:
            break

        hop += 1
        if hop > settings.max_tool_hops:
            # Runaway loop, stated in the transcript: an answer that just stops
            # reads as a crash.
            yield {
                "type": "delta",
                "text": f"\n\n_도구 호출이 {settings.max_tool_hops}회를 넘어 중단했습니다._",
            }
            break

        conversation.append(acc.assistant_message())

        # Concurrent within a hop: two searches should not queue.
        planned = [
            (index, call, by_name.get(call["name"]))
            for index, call in sorted(acc.calls.items())
        ]
        for index, call, tool in planned:
            yield {
                "type": "step",
                "id": f"h{hop}_{index}",
                "label": tool.label if tool else step_label(call["name"]),
                "status": "running",
            }

        async def execute(item: tuple[int, dict[str, Any], Tool | None]) -> ToolResult:
            _, call, tool = item
            if tool is None:
                return ToolResult(content=f"오류: 알 수 없는 도구 {call['name']}", failed=True)
            if ctx.allowed and tool.name not in ctx.allowed:
                return ToolResult(
                    content=f"오류: {tool.name} 도구가 허용되지 않았습니다.", failed=True
                )
            return await _run_tool(tool, call["arguments"], ctx)

        results = await asyncio.gather(*(execute(item) for item in planned))

        for (index, call, tool), result in zip(planned, results, strict=True):
            yield {
                "type": "step",
                "id": f"h{hop}_{index}",
                "label": tool.label if tool else step_label(call["name"]),
                "status": "error" if result.failed else "done",
                **({"detail": result.detail} if result.detail else {}),
            }
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{index}",
                    "name": call["name"],
                    "content": result.content,
                }
            )

    yield {"type": "usage", **usage}
