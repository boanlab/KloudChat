"""The tool-calling loop: model asks for a tool, the loop runs it and asks again, until prose or
`max_tool_hops`.

Tool results go back as `tool` role messages, never as instructions. A failing
tool returns its error as the result. Usage accumulates across hops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from app.core.config import settings
from app.services import settings_store
from app.services.chat import ChatStreamError, step_label, step_title
from app.services.tools.base import Tool, ToolContext, ToolResult, to_openai

log = logging.getLogger(__name__)


async def _client(api_key: str, *, redact_logging: bool = False) -> httpx.AsyncClient:
    """Client for the caller's virtual key; built per turn so the proxy URL follows the settings
    store.
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
    """Reassembles one streamed choice; tool-call fragments arrive keyed by index."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.usage = {"inputTokens": 0, "outputTokens": 0}
        self.finish_reason: str | None = None
        self.actual_model: str | None = None
        #: Stream cut off by `_is_looping`.
        self.looped = False

    def add_chunk(self, chunk: dict[str, Any]) -> str | None:
        """Returns newly emitted visible text, if any."""
        actual_model = chunk.get("model")
        if isinstance(actual_model, str) and actual_model:
            self.actual_model = actual_model
        if chunk.get("usage"):
            u = chunk["usage"]
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
    # Omitted, not defaulted: the model's own sampling applies.
    if temperature is not None:
        payload["temperature"] = temperature
    if tools:
        payload["tools"] = tool_definitions if tool_definitions is not None else to_openai(tools)
        payload["tool_choice"] = (
            {"type": "function", "function": {"name": force_tool}}
            if force_tool and any(t.name == force_tool for t in tools)
            else "auto"
        )
    if strict_local or disable_fallbacks:
        # Defence in depth beside the strict alias, which has no fallback.
        payload["disable_fallbacks"] = True

    acc = _Accumulator()
    try:
        async with await _client(api_key, redact_logging=redact_logging) as client:
            for attempt in range(len(_RETRY_AFTER) + 1):
                opened = client.stream("POST", "/v1/chat/completions", json=payload)
                response = await opened.__aenter__()
                if response.status_code == 429 and attempt < len(_RETRY_AFTER):
                    # Per-key token limits refresh by the minute.
                    await opened.__aexit__(None, None, None)
                    await asyncio.sleep(_RETRY_AFTER[attempt])
                    continue
                break
            try:
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
                        if _is_looping(acc.content):
                            # The stream is closed here; `run_turn` adds a note.
                            acc.looped = True
                            break
            finally:
                await opened.__aexit__(None, None, None)
    except httpx.HTTPError as exc:
        raise ChatStreamError(f"upstream_unreachable: {exc}") from exc

    yield "done", acc


#: Text shorter than this, written in a hop that then called tools, is
#: narration (「검색해 보겠습니다」), not an answer.
_NARRATION_CHARS = 400

#: `web_search` calls per turn; other tools keep the normal hop budget.
MAX_WEB_SEARCHES = 3


def _repeats(earlier: str, later: str) -> bool:
    """Whether `later` says what `earlier` said — same opening, or most of its lines."""
    head = re.sub(r"\s+", " ", earlier.strip())[:80]
    if head and head in re.sub(r"\s+", " ", later):
        return True
    lines = [ln.strip() for ln in earlier.splitlines() if len(ln.strip()) > 20]
    if len(lines) < 3:
        return False
    return sum(1 for ln in lines if ln in later) * 2 > len(lines)


#: Seconds to wait before retrying a 429, one per retry.
_RETRY_AFTER = (5.0, 15.0)


def _is_looping(pieces: list[str], *, window: int = 160, times: int = 4) -> bool:
    """True when the last `window` characters already appear `times` times in the recent text."""
    text = "".join(pieces[-400:])
    if len(text) < window * times:
        return False
    needle = text[-window:].strip()
    return len(needle) >= window // 2 and text.count(needle) >= times


_URL = re.compile(r"https?://[^\s)\]>\"'」』,]+")


def _urls_in(text: str) -> list[str]:
    """Every http(s) URL in `text`, trailing punctuation dropped."""
    return [u.rstrip(".,;:") for u in _URL.findall(text or "")]


def _looks_like_a_source(url: str) -> bool:
    """A URL with a path, as opposed to a home page."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return bool(parsed.netloc) and parsed.path not in ("", "/")


def _is_homepage(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return bool(parsed.netloc) and parsed.path in ("", "/")


def _source_label(url: str) -> str:
    """`host · last path segment` for a source list entry."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.")
    leaf = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
    leaf = re.sub(r"\s+", " ", leaf).strip()
    return f"{host} · {leaf[:48]}" if leaf and leaf.lower() not in {"index.html", "index"} else host


def _source_priority(url: str) -> tuple[int, str]:
    """Sort key: government, then academic, then other, then wire services."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower().split(":", 1)[0].removeprefix("www.")
    if host.endswith((".go.kr", ".gov", ".gov.uk", ".gc.ca", ".europa.eu")):
        rank = 0
    elif host.endswith((".ac.kr", ".edu", ".edu.au")):
        rank = 1
    elif any(part in host for part in ("reuters.", "apnews.", "yna.co.kr")):
        rank = 3
    else:
        rank = 2
    return rank, url


def _without_duplicate_paragraphs(text: str, *, minimum: int = 80) -> tuple[str, list[str]]:
    """`(text, removed)`: exact repeats of paragraphs at least `minimum` characters long are
    dropped.
    """
    pieces = re.split(r"(\n\s*\n)", text)
    seen: set[str] = set()
    removed: list[str] = []
    for index in range(0, len(pieces), 2):
        paragraph = pieces[index]
        key = re.sub(r"\s+", " ", paragraph).strip()
        if len(key) < minimum or key not in seen:
            if len(key) >= minimum:
                seen.add(key)
            continue
        removed.append(paragraph)
        pieces[index] = ""
        if index and pieces[index - 1]:
            pieces[index - 1] = ""
    return "".join(pieces), removed


async def _run_tool(tool: Tool, arguments: str, ctx: ToolContext) -> ToolResult:
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
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
        # The exception text may embed an unsanitised response body: log the
        # type only.
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
    #: A tool the first hop must call; later hops return to `tool_choice: auto`.
    force_tool: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Drives one assistant turn to a final answer.

    Emits `step`, `delta`, `retract`, `model_route`, `privacy_route`, and
    exactly one `usage`. `done` belongs to the caller, after credits settle.
    """
    by_name = {t.name: t for t in tools}
    conversation = list(messages)
    usage = {"inputTokens": 0, "outputTokens": 0}
    hop = 0
    redact_next_request = redact_logging
    reported_models: set[str] = set()

    def visible_label(tool: Tool | None, name: str, *, done: bool = False) -> str:
        # Progress form while running (웹 검색 중), noun when done (웹 검색).
        if done:
            label = (tool.title or tool.label) if tool else step_title(name)
        else:
            label = tool.label if tool else step_label(name)
        if sanitize_step_detail is not None:
            label, _ = sanitize_step_detail(label)
        return label

    #: The next call is the last: no tools, answer from what it has.
    closing = False
    #: Every URL a tool returned this turn.
    seen_urls: set[str] = set()
    answer_text: list[str] = []
    searches = 0
    empty_searches = 0
    #: Long text written in a hop that then called tools; retracted at the end
    #: if the final answer repeats it.
    held: list[str] = []
    while True:
        acc: _Accumulator | None = None
        hop_text: list[str] = []
        stream_kwargs: dict[str, Any] = {
            "strict_local": strict_local,
            "redact_logging": redact_next_request,
        }
        hop_tools = [] if closing else tools
        hop_definitions = [] if closing else tool_definitions
        if disable_fallbacks:
            stream_kwargs["disable_fallbacks"] = True
        # Without `tool_definitions`, `_stream_once` converts `tools` itself.
        if hop_definitions is not None:
            stream_kwargs["tool_definitions"] = hop_definitions
        if temperature is not None:
            stream_kwargs["temperature"] = temperature
        if force_tool and hop == 0:
            stream_kwargs["force_tool"] = force_tool
        async for kind, value in _stream_once(
            model,
            conversation,
            hop_tools,
            ctx.user_id,
            ctx.api_key,
            **stream_kwargs,
        ):
            if kind == "delta":
                answer_text.append(value)
                hop_text.append(value)
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

        if acc.calls and not closing and "".join(hop_text).strip():
            # Text spoken while calling tools: short is narration and goes now;
            # long may be the answer and is held until the end.
            spoken = "".join(hop_text)
            if len(spoken) < _NARRATION_CHARS:
                del answer_text[len(answer_text) - len(hop_text) :]
                yield {"type": "retract", "text": spoken}
            else:
                held.append(spoken)
        if acc.looped:
            note = (
                "\n\n_같은 내용이 되풀이되어 여기서 멈췄습니다. "
                "다시 시도하거나 다른 모델을 골라 보세요._"
            )
            answer_text.append(note)
            yield {"type": "delta", "text": note}
            break
        if closing:
            break
        if not acc.calls:
            if hop and not "".join(acc.content).strip():
                # Tools ran but the answer is empty: ask once more without tools.
                conversation.append(acc.assistant_message())
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "답이 비어 있습니다. 지금까지 모은 자료로 답을 쓰세요. "
                            "자료가 부족하면 무엇이 더 필요한지 물으세요."
                        ),
                    }
                )
                closing = True
                continue
            break

        hop += 1
        if hop > settings.max_tool_hops:
            # Hop cap: one last call with no tools, answering from what it has.
            conversation.append(acc.assistant_message())
            for index, call in sorted(acc.calls.items()):
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{index}",
                        "content": "(도구 호출 한도에 닿아 실행하지 않았습니다.)",
                    }
                )
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        "도구는 더 쓸 수 없습니다. 지금까지 모은 자료로 답을 쓰세요. "
                        "확인하지 못한 항목은 확인하지 못했다고 밝히세요."
                    ),
                }
            )
            closing = True
            continue

        conversation.append(acc.assistant_message())

        # Tool calls within a hop run concurrently.
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
                    # The next request carries privacy labels: redact its log.
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
                # Strict-local: the model sees the raw result, but the
                # persisted timeline detail is sanitised.
                result.detail, _ = sanitize_step_detail(result.detail)
            if finding_counts and sanitize_tool_output is None:
                # Strict-local hop with findings: LiteLLM's log must still redact it.
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
            seen_urls.update(_urls_in(result.content))
            if call["name"] == "web_search":
                searches += 1
                empty_searches += int(result.empty)

        if searches >= MAX_WEB_SEARCHES:
            conversation.append(
                {
                    "role": "user",
                    "content": (
                        "웹 검색은 충분히 했습니다. 도구를 더 쓰지 말고 지금까지 "
                        "확인한 자료로 답하세요. 확인하지 못한 항목은 그렇게 밝히고, "
                        "실제 검색 결과에 있던 URL만 출처로 쓰세요."
                    ),
                }
            )
            closing = True

    # Post-processing: retract repeated held text and duplicate paragraphs,
    # then annotate the answer's URLs against `seen_urls`.
    answer = "".join(answer_text)
    for spoken in held:
        at = answer.find(spoken)
        final = answer[at + len(spoken) :] if at >= 0 else ""
        if _repeats(spoken, final):
            answer = answer.replace(spoken, "", 1)
            yield {"type": "retract", "text": spoken}
    answer, duplicate_paragraphs = _without_duplicate_paragraphs(answer)
    for paragraph in duplicate_paragraphs:
        yield {"type": "retract", "text": paragraph}
    answer_text[:] = [answer]
    if searches and empty_searches * 2 >= searches and answer.strip():
        note = (
            "\n\n_웹 검색이 쓸 만한 결과를 주지 않아 이 답은 검색으로 확인하지 못했습니다. "
            "서지·수치·최신 사항은 확인이 필요합니다._"
            if empty_searches == searches
            else "\n\n_웹 검색 결과가 대부분 질문과 무관해 이 답은 충분히 확인되지 않았습니다. "
            "서지·수치·최신 사항은 확인이 필요합니다._"
        )
        answer_text.append(note)
        yield {"type": "delta", "text": note}
    verified_in_answer = {u for u in _urls_in(answer) if u in seen_urls}
    source_urls = sorted(
        (u for u in seen_urls if _looks_like_a_source(u)),
        key=_source_priority,
    )
    if searches and source_urls and not verified_in_answer:
        # Only URLs a tool returned are appended.
        appendix = "\n\n### 확인한 출처\n" + "\n".join(
            f"- [{_source_label(url)}]({url})" for url in source_urls[:5]
        )
        answer_text.append(appendix)
        yield {"type": "delta", "text": appendix}
    unverified = [u for u in _urls_in(answer) if u not in seen_urls and _looks_like_a_source(u)]
    if unverified and seen_urls:
        note = (
            "\n\n_다음 링크는 이 답을 쓰며 검색·열람한 결과에 없던 것입니다. 기억으로 적은 "
            "것이니 열어 보고 확인하세요: " + ", ".join(dict.fromkeys(unverified)) + "_"
        )
        answer_text.append(note)
        yield {"type": "delta", "text": note}
    homepages = [u for u in _urls_in(answer) if _is_homepage(u)]
    if searches and homepages:
        note = (
            "\n\n_기관 홈페이지 첫 화면은 위 주장을 뒷받침하는 직접 출처가 아닙니다. "
            "해당 보고서·보도자료의 원문 주소를 확인하세요: "
            + ", ".join(dict.fromkeys(homepages))
            + "_"
        )
        answer_text.append(note)
        yield {"type": "delta", "text": note}

    yield {"type": "usage", **usage}
