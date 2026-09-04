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
        #: Cut off for repeating itself — see `_is_looping`.
        self.looped = False

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
            for attempt in range(len(_RETRY_AFTER) + 1):
                opened = client.stream("POST", "/v1/chat/completions", json=payload)
                response = await opened.__aenter__()
                if response.status_code == 429 and attempt < len(_RETRY_AFTER):
                    # 한도에 걸리면 조금 기다렸다 다시 한다.
                    #
                    # A per-key token limit refreshes by the minute; the person
                    # who hit it got 「모델 응답을 받지 못했습니다」 in under a
                    # second, with nothing to do but press 다시 시도 — which this
                    # does for them, twice, before giving up.
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
                            # 같은 문단을 되풀이하면 끊는다.
                            #
                            # A small model once wrote the same two sentences
                            # about tax invoices two hundred times — 64k tokens,
                            # thirteen minutes — before anything stopped it.
                            # The stream is closed here; the caller adds a line.
                            acc.looped = True
                            break
            finally:
                await opened.__aexit__(None, None, None)
    except httpx.HTTPError as exc:
        raise ChatStreamError(f"upstream_unreachable: {exc}") from exc

    yield "done", acc


#: Text shorter than this, written in a hop that then called tools, is
#: narration — 「코드를 작성했습니다. 이제 실행해 보겠습니다.」 — not an answer.
_NARRATION_CHARS = 400

# A search call already fans out into several fetched pages. Three calls give
# the model enough independent result sets for a fact-check; beyond that the
# measured behaviour was six near-identical searches and no answer after four
# minutes. Other tools retain the normal hop budget.
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
    """True when the tail of the text has already appeared `times` times.

    Cheap enough to run per chunk: a slice, then one `count` over what has
    been written so far. Checked only once the text is long enough for a
    genuine repeat — a short answer that says 「네」 twice is not a loop.
    """
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
    """A link that stands for a source — a paper, a page — rather than a home page."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return bool(parsed.netloc) and parsed.path not in ("", "/")


def _source_label(url: str) -> str:
    """A compact, informative label for a URL preserved from a tool result."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    host = parsed.netloc.removeprefix("www.")
    leaf = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
    leaf = re.sub(r"\s+", " ", leaf).strip()
    return f"{host} · {leaf[:48]}" if leaf and leaf.lower() not in {"index.html", "index"} else host


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

    #: Set when the hop cap has been reached: the next call is the last, it
    #: gets no tools, and it is told to answer from what it has.
    closing = False
    #: Every URL a tool returned this turn, and everything the model wrote.
    seen_urls: set[str] = set()
    answer_text: list[str] = []
    #: Searches run this turn, and how many of them found nothing.
    searches = 0
    empty_searches = 0
    #: Text the model wrote in a hop that went on to call tools — 「검색해
    #: 보겠습니다」, or a whole answer it then repeated after the tool came
    #: back. Short ones are taken off the screen at once; long ones only if
    #: the final answer says the same thing again. See `retract` below.
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
        # Tests and third-party extensions that call ``run_turn`` directly can
        # keep the legacy conversion path.  Production passes the preflighted
        # snapshot so every hop sends byte-for-byte equivalent definitions.
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
            # 도구를 부르며 한 말은 중계다.
            #
            # The step row already says 웹 검색 중; the sentence saying so
            # under it is noise, and when the model wrote its whole answer
            # before running the check it then wrote the answer again after.
            # A short piece goes now. A long one is a possible answer, and is
            # kept unless the final text repeats it — decided at the end.
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
                # 도구는 썼는데 답이 비었다.
                #
                # Two searches, three pages read, then a completion with no
                # text and no calls — the person waited three minutes for
                # 「답이 오지 않았습니다」. Ask once more, with no tools, the
                # way the hop cap does; the material is all in the conversation.
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
            # 한도에 닿으면 답을 쓰게 한다.
            #
            # This used to print 「도구 호출이 5회를 넘어 중단했습니다」 and
            # stop — and since a model that is still calling tools has
            # written no prose yet, the person got that one line and nothing
            # else: a fact-check that ran five searches and reported none of
            # them. The last hop now runs with no tools and one instruction:
            # answer from what you have, and say what you could not check.
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

    # 검색에 없던 링크는 그렇다고 말한다.
    #
    # A literature survey came back with eleven arXiv links, six of which
    # did not exist — plausible ids the model wrote from memory, under real
    # authors' names with the wrong years. A reader has no way to tell those
    # from the five that came out of a search. The turn knows: every URL a
    # tool returned is in `seen_urls`, so a URL in the answer that is not
    # there is one the model made up or remembered, and it is listed as such.
    answer = "".join(answer_text)
    for spoken in held:
        # The final answer opens the way the held text did: the model said
        # it twice, once before the tool and once after. The first goes.
        at = answer.find(spoken)
        final = answer[at + len(spoken) :] if at >= 0 else ""
        if _repeats(spoken, final):
            answer = answer.replace(spoken, "", 1)
            yield {"type": "retract", "text": spoken}
    answer_text[:] = [answer]
    if searches and empty_searches * 2 >= searches and answer.strip():
        # 검색이 전부 빈손이었으면 답 밑에 그렇다고 적는다.
        #
        # The tool told the model to say so; the model, handed a literature
        # question it knew the answer to, wrote five real papers and forgot.
        # The reader is the one who has to know that nothing here was checked.
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
    source_urls = sorted(u for u in seen_urls if _looks_like_a_source(u))
    if searches and source_urls and not verified_in_answer:
        # Models occasionally use the evidence correctly but omit its links.
        # Keep provenance deterministic: append only URLs that a tool actually
        # returned, never a title or address reconstructed from model memory.
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

    yield {"type": "usage", **usage}
