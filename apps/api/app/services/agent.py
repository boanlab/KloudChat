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
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store
from app.services.chat import ChatStreamError, step_label, step_title
from app.services.tools.base import Tool, ToolContext, ToolResult, to_openai

log = logging.getLogger(__name__)


async def _client(api_key: str, *, redact_logging: bool = False) -> httpx.AsyncClient:
    """Built per turn, so an administrator can repoint the proxy live.

    `api_key` is the caller's virtual key, so the proxy attributes and limits the
    turn against that person.
    """
    base, _ = await settings_store.litellm_config()
    return httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={
            "Authorization": f"Bearer {api_key}",
            **({"x-litellm-enable-message-redaction": "true"} if redact_logging else {}),
        },
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
        self.actual_model: str | None = None

    def add_chunk(self, chunk: dict[str, Any]) -> str | None:
        """Returns newly emitted visible text, if any."""
        actual_model = chunk.get("model")
        if isinstance(actual_model, str) and actual_model:
            self.actual_model = actual_model
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
                call = self.calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
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
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
    force_tool: str | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Yields `('delta', text)` while streaming, then `('done', _Accumulator)`."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "user": user_id,
    }
    # Omitted rather than defaulted: a turn nobody set a temperature for should
    # sample the way the model ships, and every hop of the same turn has to
    # sample alike or the answer changes voice halfway through.
    if temperature is not None:
        payload["temperature"] = temperature
    if tools:
        payload["tools"] = tool_definitions if tool_definitions is not None else to_openai(tools)
        # `auto` everywhere except a caller that named a tool for this hop. See
        # `run_turn`'s `force_tool` for why one hop and not the whole turn.
        payload["tool_choice"] = (
            {"type": "function", "function": {"name": force_tool}}
            if force_tool and any(t.name == force_tool for t in tools)
            else "auto"
        )
    if strict_local or disable_fallbacks:
        # Defence in depth. The strict alias has no fallback in KloudChat-LLM;
        # this also asks LiteLLM's router not to fall back for this request.
        payload["disable_fallbacks"] = True

    acc = _Accumulator()
    try:
        async with await _client(api_key, redact_logging=redact_logging) as client:
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
        # Remote tool exceptions occasionally embed their response body. That
        # body has not reached the outbound sanitizer yet, so neither the log
        # nor the model-facing error may copy it verbatim.
        error_type = type(exc).__name__
        log.warning("tool %s failed (%s)", tool.name, error_type)
        return ToolResult(
            content=f"오류: {tool.name} 실행에 실패했습니다 ({error_type}).",
            failed=True,
        )

    if isinstance(output, ToolResult):
        return output
    return ToolResult(content=str(output))


async def run_turn(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[Tool],
    ctx: ToolContext,
    sanitize_tool_output: Callable[[str], tuple[str, int]] | None = None,
    sanitize_step_detail: Callable[[str], tuple[str, int]] | None = None,
    classify_tool_output: Callable[[str], list[dict[str, Any]]] | None = None,
    strict_local: bool = False,
    disable_fallbacks: bool = False,
    redact_logging: bool = False,
    tool_definitions: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    #: A tool the first hop must call, rather than may.
    #:
    #: `tool_choice: auto` is the right default and the wrong one for a control
    #: somebody switched on. A small model reads the search nudge as advice: it
    #: answers from memory, and the answer arrives under a lit globe that says
    #: it was looked up. The person then has to know enough to disbelieve it and
    #: type "인터넷 검색해봐" — which only works for the facts they already
    #: doubted.
    #:
    #: Forced for one hop, not the turn. After the first call `tool_choice`
    #: returns to `auto`, so the model is free to search again, use another
    #: tool, or answer — the requirement is that it looks before it writes, not
    #: that it keeps looking. Forcing the whole turn cannot terminate: every hop
    #: would owe another call.
    force_tool: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Drives one assistant turn to a final answer.

    Emits `step` (running/done/error), `delta`, and exactly one `usage`. `done`
    belongs to the caller, after credits settle.
    """
    by_name = {t.name: t for t in tools}
    conversation = list(messages)
    usage = {"inputTokens": 0, "outputTokens": 0}
    hop = 0
    redact_next_request = redact_logging
    reported_models: set[str] = set()

    def visible_label(tool: Tool | None, name: str, *, done: bool = False) -> str:
        # Progress form while it runs, noun once it is over: 웹 검색 중 under a
        # spinner, 웹 검색 beside the check. One string for both left every
        # finished row saying it was still running.
        if done:
            label = (tool.title or tool.label) if tool else step_title(name)
        else:
            label = tool.label if tool else step_label(name)
        if sanitize_step_detail is not None:
            label, _ = sanitize_step_detail(label)
        return label

    while True:
        acc: _Accumulator | None = None
        stream_kwargs: dict[str, Any] = {
            "strict_local": strict_local,
            "redact_logging": redact_next_request,
        }
        if disable_fallbacks:
            stream_kwargs["disable_fallbacks"] = True
        # Tests and third-party extensions that call ``run_turn`` directly can
        # keep the legacy conversion path.  Production passes the preflighted
        # snapshot so every hop sends byte-for-byte equivalent definitions.
        if tool_definitions is not None:
            stream_kwargs["tool_definitions"] = tool_definitions
        if temperature is not None:
            stream_kwargs["temperature"] = temperature
        if force_tool and hop == 0:
            stream_kwargs["force_tool"] = force_tool
        async for kind, value in _stream_once(
            model,
            conversation,
            tools,
            ctx.user_id,
            ctx.api_key,
            **stream_kwargs,
        ):
            if kind == "delta":
                yield {"type": "delta", "text": value}
            else:
                acc = value
        assert acc is not None

        usage["inputTokens"] += acc.usage["inputTokens"]
        usage["outputTokens"] += acc.usage["outputTokens"]
        if acc.actual_model and acc.actual_model not in reported_models:
            reported_models.add(acc.actual_model)
            yield {
                "type": "model_route",
                "routedModel": model,
                "actualModel": acc.actual_model,
            }

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
            (index, call, by_name.get(call["name"])) for index, call in sorted(acc.calls.items())
        ]
        for index, call, tool in planned:
            yield {
                "type": "step",
                "id": f"h{hop}_{index}",
                "label": visible_label(tool, call["name"]),
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
            finding_counts: dict[tuple[str, str], int] = {}

            def collect(
                raw: str,
                counts: dict[tuple[str, str], int] = finding_counts,
            ) -> None:
                if classify_tool_output is None:
                    return
                for finding in classify_tool_output(raw):
                    key = (
                        str(finding.get("category") or "unknown"),
                        str(finding.get("source") or "tool_output"),
                    )
                    counts[key] = counts.get(key, 0) + int(finding.get("count") or 0)

            collect(result.content)
            if result.detail:
                collect(result.detail)
            if sanitize_tool_output is not None:
                result.content, protected = sanitize_tool_output(result.content)
                if result.detail:
                    result.detail, detail_protected = sanitize_tool_output(result.detail)
                    protected += detail_protected
                if protected:
                    # The raw value is never placed in ``conversation``. The
                    # next proxy request still carries privacy labels, so turn
                    # on LiteLLM message redaction before that hop is logged.
                    redact_next_request = True
                    yield {
                        "type": "privacy_route",
                        "action": "mask_external",
                        "source": "tool_output",
                        "count": protected,
                        "findings": [
                            {"category": category, "source": source, "count": count}
                            for (category, source), count in sorted(finding_counts.items())
                        ],
                    }
            elif sanitize_step_detail is not None and result.detail:
                # A strict-local model may use the raw tool result internally,
                # but the timeline is persisted. Sanitize its display-only
                # detail without changing what the model receives.
                result.detail, _ = sanitize_step_detail(result.detail)
            if finding_counts and sanitize_tool_output is None:
                # A strict-local hop may consume the raw result, but LiteLLM's
                # own message/spend log must still redact that sensitive hop.
                redact_next_request = True
                yield {
                    "type": "privacy_route",
                    "action": "strict_local",
                    "source": "tool_output",
                    "count": sum(finding_counts.values()),
                    "findings": [
                        {"category": category, "source": source, "count": count}
                        for (category, source), count in sorted(finding_counts.items())
                    ],
                }
            yield {
                "type": "step",
                "id": f"h{hop}_{index}",
                "label": visible_label(tool, call["name"], done=True),
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
