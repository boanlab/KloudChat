"""Sanitize text-format tool-call leaks into OpenAI `tool_calls`, strip Llama
PUA/turn-trailer garbage, and substitute CJK Han into Korean readings.

Tool-call leak shapes detected:
    1. Llama 3.x special-token: `<|python_tag|>{"name": "...", "parameters": ...}`
    2. Markdown code-block:     ```json\\n{...}\\n``` (or no `json` lang tag)
    3. Labelled bare JSON:      `**Call Function:** {...}` / `Tool: {...}` / etc.

Trailing-garbage shapes stripped:
    4. PUA-prefixed trailer:    U+E200–U+E3FF then `turn{N}<tool>{M}...`
    5. Naked turn-trailer:      `turn{0}{search}{0}` (no PUA prefix; Ollama variant)
    6. Naked tool-pair trailer: `{search}{0}` / `{youtube}{0}` (turn 누락 변형)

For CJK Han characters mixed into Korean output (qwen 계열에서 흔함), code-fence
밖의 한자를 한글 음독으로 치환 (例: 大韓民國 → 대한민국). fence 안은 보존.

All sanitization happens in two CustomLogger hooks — `async_post_call_success_hook`
for non-streaming, `async_post_call_streaming_iterator_hook` for streaming. The
stream variant runs a small state machine: buffers up to 64 chars or until a
turn-trailer prefix is seen, then either converts to tool_calls / strips garbage /
releases as-is depending on what materialises.

Conservative trigger for fence/bare-JSON paths — only convert when parsed JSON has
top-level `name` (string) + `parameters`/`arguments` field, to avoid eating
legitimate JSON examples in chat output.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator

from litellm.integrations.custom_logger import CustomLogger

# 한자 → 한글 음 변환. qwen 등 중국계 모델이 한국어 응답 중간에 한자를 섞는 leak 을
# 자연스러운 음독으로 대체 ("大韓民國" → "대한민국"). 단순 제거 시 문장 깨짐 회피.
try:
    import hanja as _hanja
except Exception:  # pragma: no cover — 사전 install 안 된 환경 대비
    _hanja = None

# CJK Unified Ideographs (Basic, Extension A, Compatibility) 단일 매치.
# 코드 fence 안은 보존하기 위해 fence-aware 헬퍼에서 segment 단위로 호출.
CJK_HAS_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
FENCE_SPLIT_RE = re.compile(r"(```[\s\S]*?```)")


def _han_to_kor(text: str) -> str:
    """code fence 밖의 한자만 한글 음독으로 변환. fence 안은 그대로 유지."""
    if not text or _hanja is None or not CJK_HAS_RE.search(text):
        return text
    parts = FENCE_SPLIT_RE.split(text)
    for i, p in enumerate(parts):
        if i % 2 == 0 and CJK_HAS_RE.search(p):  # 짝수 인덱스 = fence 바깥
            parts[i] = _hanja.translate(p, "substitution")
    return "".join(parts)


def _filter_cjk(chunk: Any) -> Any:
    """streaming chunk in-place mutate: delta.content 의 한자만 한글로."""
    if getattr(chunk, "choices", None):
        d = getattr(chunk.choices[0], "delta", None)
        if d is not None:
            c = getattr(d, "content", None)
            if isinstance(c, str) and CJK_HAS_RE.search(c):
                d.content = _han_to_kor(c)
    return chunk

log = logging.getLogger("litellm-python-tag-sanitizer")

# Llama 3.x reserved-special-token range (Private Use Area U+E200-U+E3FF). The
# tokenizer normally consumes these and never surfaces them. When they DO leak
# (e.g. Ollama's chat-template strips wrong, or the model emits them mid-text),
# they trail with structured-but-garbage text like `turn{1}search{0}.`.
# The actual answer is always BEFORE the first PUA char — strip everything from
# the first occurrence onward.
PUA_TRAILING_RE = re.compile(r"[-].*", re.DOTALL)

# Naked Llama "turn{N}{<tool>}{M}" trailer — emitted as plain ASCII without
# a PUA prefix on some Ollama builds. Variants seen in the wild:
#   turn{0}{search}{0}
#   turn{0}{youtube}{0}
#   turn{0}{search}{}{}{0}    (empty braces, extra groups)
# Pattern: literal "turn" followed by one-or-more `{...}` groups (each may
# contain any non-`}` chars, including empty).
TURN_GLOBAL_RE = re.compile(r"\s*turn(?:\{[^}]*\})+\.?", re.DOTALL)
TURN_PARTIAL_RE = re.compile(
    r"^\s*turn(?:\{[^}]*\})*(?:\{[^}]*)?$",
    re.DOTALL,
)
TURN_START_RE = re.compile(r"\s*turn(\{|$)", re.DOTALL)

# 'turn' prefix 없이 `{search}{0}`, `{youtube}{0}` 같은 brace-pair 만 단독 leak 되는 변형.
# tool 이름이 들어가는 첫 brace + 인덱스(또는 빈 brace) 페어가 1회 이상.
NAKED_TOOL_GLOBAL_RE = re.compile(
    r"\s*\{(?:search|youtube|tool|browse|fetch)\}(?:\{[^}]*\})+\.?",
    re.DOTALL,
)


def _strip_pua_trailer(text: str) -> str:
    """Strip Llama special-token leak: PUA char + trailing garbage like
    turn{1}search{0}, OR the naked ASCII variant of the same pattern.
    Naked variant matches anywhere in text (not just trailing) since the model
    sometimes emits it mid-response then continues with real content."""
    text = PUA_TRAILING_RE.sub("", text)
    text = TURN_GLOBAL_RE.sub("", text)
    text = NAKED_TOOL_GLOBAL_RE.sub("", text)
    return text

# Sentinel patterns. We scan for either marker, then brace-count the trailing
# JSON object (handles nested braces, string literals with `}` inside).
TAG = "<|python_tag|>"
# Either of:
#   <|python_tag|>
#   ```json\n  / ```\n   (markdown code fence; the closing fence is found later)
SENTINEL_RE = re.compile(
    r"(?P<tag><\|python_tag\|>)|(?P<fence>```(?:json|JSON)?[ \t]*\r?\n?)"
)


def _scan_json_object(text: str, start: int) -> int | None:
    """Brace-count a JSON object starting at the first `{` at or after `start`.
    Returns the index AFTER the closing `}`, or None if not found / unbalanced."""
    j = start
    n = len(text)
    while j < n and text[j].isspace():
        j += 1
    if j >= n or text[j] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    k = j
    while k < n:
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
        k += 1
    return None


def _looks_like_tool_call(payload, *, strict: bool = False) -> bool:
    """True if payload smells like a function-call JSON object.
    `strict=True` additionally requires `parameters`/`arguments` — used for
    fence/bare-JSON paths where the surrounding context is weaker than the
    explicit `<|python_tag|>` sentinel."""
    if not (isinstance(payload, dict) and isinstance(payload.get("name"), str) and payload["name"]):
        return False
    if strict and not ("parameters" in payload or "arguments" in payload):
        return False
    return True


def _extract_blocks(text: str) -> tuple[list[dict], str]:
    """Return (parsed_payloads, cleaned_text). Handles balanced braces.
    For markdown code fences, requires payload to look like a tool call
    (has a top-level `name` string) before consuming — keeps legit JSON examples intact."""
    out: list[dict] = []
    cleaned_parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        m = SENTINEL_RE.search(text, i)
        if not m:
            cleaned_parts.append(text[i:])
            break
        is_fence = m.group("fence") is not None
        # text before sentinel — always kept
        cleaned_parts.append(text[i:m.start()])
        json_start = m.end()
        json_end = _scan_json_object(text, json_start)
        if json_end is None:
            # malformed — keep literal sentinel and continue past it
            cleaned_parts.append(text[m.start():m.end()])
            i = m.end()
            continue
        chunk = text[json_start:json_end].lstrip()
        try:
            payload = json.loads(chunk)
        except Exception:
            cleaned_parts.append(text[m.start():json_end])
            i = json_end
            continue

        if is_fence and not _looks_like_tool_call(payload, strict=True):
            # Plain JSON code block — leave untouched.
            cleaned_parts.append(text[m.start():json_end])
            i = json_end
            continue

        # Consume the JSON. For fences, also consume the trailing ``` if present.
        consume_end = json_end
        if is_fence:
            tail = text[json_end:]
            close = re.match(r"\s*```", tail)
            if close:
                consume_end = json_end + close.end()
        if _looks_like_tool_call(payload):
            out.append(payload)
        else:
            # python_tag with payload missing `name` — keep raw text rather than drop silently
            cleaned_parts.append(text[m.start():consume_end])
        i = consume_end

    text_out = "".join(cleaned_parts)

    # Fallback: if no sentinel-tagged blocks were found, scan for "bare" JSON
    # objects that look like tool calls. Required to catch leaks like:
    #     **Call Function:** {"name": "web_search", "parameters": {...}}
    #     Tool: {"name": "...", "arguments": {...}}
    # Conservative trigger:
    #   - JSON must parse and have a top-level `name` (string) field
    #   - JSON must have `parameters` OR `arguments` (string or dict)
    #   - The remaining text (after stripping known label prefixes + whitespace)
    #     must be only the JSON itself — no substantive prose around it.
    if not out:
        scan = text_out.strip()
        # Strip common labels seen in the wild
        scan = re.sub(
            r"^(?:\*+\s*)?(?:Call\s*Function|Function\s*Call|Tool\s*Call|Tool|Action|Function)\s*[:：]\s*(?:\*+\s*)?",
            "", scan, flags=re.IGNORECASE,
        ).strip()
        if scan.startswith("{"):
            end = _scan_json_object(scan, 0)
            if end is not None and not scan[end:].strip():
                try:
                    payload = json.loads(scan[:end])
                except Exception:
                    payload = None
                if isinstance(payload, dict) and _looks_like_tool_call(payload, strict=True):
                    out.append(payload)
                    text_out = ""

    if out:
        # Collapse separator residue (",", ";", whitespace) left between/after extracted blocks.
        text_out = re.sub(r"[\s,;]+", " ", text_out).strip(" ,;\t\n")
    return out, text_out


def _payload_to_tool_call(payload: dict, idx: int) -> dict | None:
    name = payload.get("name")
    if not name:
        return None
    args = payload.get("parameters", payload.get("arguments", {}))
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "index": idx,
        "function": {"name": name, "arguments": args},
    }


class PythonTagSanitizer(CustomLogger):
    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        try:
            choices = getattr(response, "choices", None) or []
            for choice in choices:
                msg = getattr(choice, "message", None)
                content = getattr(msg, "content", None) if msg else None
                if not msg or not isinstance(content, str) or not content:
                    continue

                # 1. PUA trailer (Llama special-token leak — turn{1}search{0} etc.)
                stripped = _strip_pua_trailer(content)
                if stripped != content:
                    log.warning(
                        "python-tag: stripped PUA trailing garbage (model=%s)",
                        getattr(response, "model", "?"),
                    )
                # 1b. 한자 → 한글 음독 (code fence 보존).
                hanged = _han_to_kor(stripped)
                if hanged != stripped:
                    log.info(
                        "python-tag: han→kor substituted (model=%s)",
                        getattr(response, "model", "?"),
                    )
                stripped = hanged
                content = stripped

                # 2. Tool-call leak extraction (python_tag / md fence / bare-JSON)
                payloads, cleaned = _extract_blocks(content)
                if payloads:
                    existing = list(getattr(msg, "tool_calls", None) or [])
                    new_calls = []
                    for i, p in enumerate(payloads):
                        tc = _payload_to_tool_call(p, len(existing) + i)
                        if tc:
                            new_calls.append(tc)
                    if new_calls:
                        msg.tool_calls = existing + new_calls
                        msg.content = cleaned.strip() or None
                        if getattr(choice, "finish_reason", None) in (None, "stop"):
                            choice.finish_reason = "tool_calls"
                        log.warning(
                            "python-tag: sanitized %d block(s) in non-stream response (model=%s)",
                            len(new_calls), getattr(response, "model", "?"),
                        )
                        continue
                # Only PUA stripped, no JSON extraction — still update content if changed
                if stripped != msg.content:
                    msg.content = stripped or None
        except Exception:
            log.exception("python-tag sanitizer (non-stream) error")
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data
    ) -> AsyncGenerator[Any, None]:
        buffer = ""
        held: list[Any] = []
        passthrough = False
        pua_seen = False

        # Naked turn-trailer state machine. Llama 가 `turn{N}{<tool>}{M}` 를
        # 평문 ASCII 로 leak 하는 경우, 패턴이 여러 chunk 에 걸쳐 split 돼서 들어옴
        # (예: "turn" → "{0}" → "{search}" → "{0}"). 'turn' chunk 발견 시 hold 모드
        # 진입 → 후속 chunk 누적해서 패턴 완성되면 모든 hold 된 chunk content 소거,
        # 매치 안 되면 (다른 합법 텍스트로 이어지면) 그대로 release.
        turn_hold = False
        turn_buf = ""
        turn_held: list[Any] = []

        try:
            async for chunk in response:
                if getattr(chunk, "choices", None):
                    ch0 = chunk.choices[0]
                    delta = getattr(ch0, "delta", None)
                    text = getattr(delta, "content", None) if delta else None
                else:
                    delta = None
                    text = None

                # 1. PUA trailing strip — always (independent of turn state)
                if isinstance(text, str) and text and delta is not None:
                    if pua_seen:
                        delta.content = None
                        text = None
                    else:
                        stripped = PUA_TRAILING_RE.sub("", text)
                        if stripped != text:
                            pua_seen = True
                            delta.content = stripped or None
                            text = stripped or None
                            log.warning(
                                "python-tag: stripped PUA trailing (model=%s)",
                                getattr(chunk, "model", "?"),
                            )

                # 2. Naked turn-trailer state machine
                if isinstance(text, str) and text:
                    if turn_hold:
                        turn_buf += text
                        turn_held.append(chunk)
                        if TURN_GLOBAL_RE.match(turn_buf):
                            # Pattern complete — strip the matched prefix.
                            m = TURN_GLOBAL_RE.match(turn_buf)
                            rest = turn_buf[m.end():]
                            # All held chunks' content was part of turn-trailer +
                            # possibly trailing rest. Wipe all held content; if
                            # rest is non-empty, attach to last held chunk.
                            for c in turn_held[:-1]:
                                if getattr(c, "choices", None):
                                    d = getattr(c.choices[0], "delta", None)
                                    if d is not None:
                                        d.content = None
                            last = turn_held[-1]
                            if getattr(last, "choices", None):
                                d = getattr(last.choices[0], "delta", None)
                                if d is not None:
                                    d.content = rest if rest else None
                            log.warning(
                                "python-tag: stripped naked turn-trailer (model=%s, rest_len=%d)",
                                getattr(chunk, "model", "?"), len(rest),
                            )
                            flushed = list(turn_held)
                            turn_hold = False
                            turn_buf = ""
                            turn_held = []
                            for fc in flushed:
                                if passthrough:
                                    yield _filter_cjk(fc)
                                else:
                                    held.append(fc)
                            continue
                        elif TURN_PARTIAL_RE.match(turn_buf) and len(turn_held) < 12:
                            # Still potential — keep holding (cap at 12 chunks
                            # to bound memory + latency).
                            continue
                        else:
                            # False alarm — release as-is.
                            log.warning(
                                "python-tag: turn-trailer false alarm, releasing %d chunk(s) buf=%r",
                                len(turn_held), turn_buf[:60],
                            )
                            flushed = list(turn_held)
                            turn_hold = False
                            turn_buf = ""
                            turn_held = []
                            for fc in flushed:
                                if passthrough:
                                    yield _filter_cjk(fc)
                                else:
                                    held.append(fc)
                            # Fall through to handle CURRENT chunk normally
                    elif TURN_START_RE.search(text):
                        # Found "turn" — split chunk at the "turn" position,
                        # yield the pre-turn part immediately, start holding from
                        # "turn" onward.
                        idx = text.find("turn")
                        pre = text[:idx]
                        post = text[idx:]
                        if pre:
                            # Aliasing trade-off: the chunk object is yielded with
                            # delta.content=pre, then mutated to delta.content=post
                            # and re-used as the first turn_held entry. Consumer
                            # captures pre-content at yield time; later mutation
                            # only affects the held copy. False-alarm releases the
                            # post part (pre was already yielded).
                            delta.content = pre
                            if passthrough:
                                yield _filter_cjk(chunk)
                            else:
                                held.append(chunk)
                                buffer += pre
                            delta.content = post
                            turn_hold = True
                            turn_buf = post
                            turn_held = [chunk]
                        else:
                            turn_hold = True
                            turn_buf = text
                            turn_held = [chunk]
                        continue

                if passthrough:
                    yield _filter_cjk(chunk)
                    continue
                delta_text = ""
                finish = None
                if getattr(chunk, "choices", None):
                    ch0 = chunk.choices[0]
                    delta = getattr(ch0, "delta", None)
                    if delta is not None and getattr(delta, "content", None):
                        delta_text = delta.content
                    finish = getattr(ch0, "finish_reason", None)
                buffer += delta_text
                held.append(chunk)
                if TAG not in buffer and (len(buffer) >= 64 or finish):
                    # No tag in the buffered prefix and we have enough signal —
                    # flush and switch to direct streaming.
                    for hc in held:
                        yield _filter_cjk(hc)
                    held = []
                    passthrough = True

            # Stream ended. If we held back, decide.
            if held:
                if TAG in buffer:
                    payloads, cleaned = _extract_blocks(buffer)
                    if payloads:
                        new_calls = []
                        for i, p in enumerate(payloads):
                            tc = _payload_to_tool_call(p, i)
                            if tc:
                                new_calls.append(tc)
                        if new_calls:
                            last = held[-1]
                            if getattr(last, "choices", None):
                                ch0 = last.choices[0]
                                delta = getattr(ch0, "delta", None)
                                if delta is not None:
                                    delta.content = cleaned.strip() or None
                                    delta.tool_calls = [
                                        {
                                            "index": tc["index"],
                                            "id": tc["id"],
                                            "type": "function",
                                            "function": tc["function"],
                                        }
                                        for tc in new_calls
                                    ]
                                ch0.finish_reason = "tool_calls"
                            log.warning(
                                "python-tag: sanitized %d block(s) in stream (model=%s)",
                                len(new_calls), getattr(last, "model", "?"),
                            )
                            yield _filter_cjk(last)
                            return
                # No conversion — flush held as-is
                for hc in held:
                    yield _filter_cjk(hc)
        except Exception:
            log.exception("python-tag sanitizer (stream) error")
            for hc in held:
                try:
                    yield _filter_cjk(hc)
                except Exception:
                    pass


sanitizer_instance = PythonTagSanitizer()


# Self-register on import. The proxy's `litellm_settings.callbacks` config entry
# imports this module but doesn't reliably append the resolved instance to
# `litellm.callbacks` (the list that hook dispatch walks). Appending here on
# import side-steps that path.
try:
    import litellm as _litellm
    if not isinstance(_litellm.callbacks, list):
        _litellm.callbacks = []
    if sanitizer_instance not in _litellm.callbacks:
        _litellm.callbacks.append(sanitizer_instance)
except Exception:
    log.exception("python-tag: self-registration failed")
