"""Trim oldest history + clamp output cap + inject stub-user, so requests fit
a deployment's actual ctx instead of letting vLLM return raw 400.

Why this exists
---------------
`librechat.yaml` sets `summarization.enabled: false` because the local chat
model treats a mid-stream summary block as prior-session memory and
hallucinates against it. So LibreChat sends the full history every turn. Local
deployments cap ctx (vLLM `--max-model-len`) for KV cache memory; tool-heavy
agents (Deep Research / LDR) also send `max_completion_tokens=30000` and ReAct
follow-ups without a user role. This hook addresses four failure modes in one
pre-call hook:

  1. Input exceeds ctx → trim oldest history (or return friendly error).
  2. Output cap exceeds smallest deployment's max_model_len → clamp.
  3. Static cap looks safe but input+output overflows at runtime → clamp
     using input_tokens-aware formula.
  4. Messages have no user role (local chat-template hard-rejects) → inject stub.

ctx maps (`_load_ctx_maps` returns max + min per model_name)
-------------------------------------------------------------
`gen-litellm-config.sh` emits two fields per deployment:

  * `actual_ctx_tokens` — the deployment's real `max_model_len` (used here
    as the trim budget).
  * `max_input_tokens` — declared/conservative (ctx − `KC_PRE_CALL_HEADROOM`,
    default 4K) so the router's `enable_pre_call_checks` reserves space for
    tools schema + chat-template wrappers.

When a model_name has multiple deployments (e.g. 16K + 128K node mix) we
keep both extremes:

  * MAX = trim budget — preserves most history; router will route to the
    deployment that fits.
  * MIN = output clamp — vLLM static-rejects when `max_completion_tokens
    >= max_model_len`, and the router can route any short-input call to
    *any* deployment, so the clamp must clear the smallest.

A model with no ctx fields (commercial OR routes, super-agent, embeddings)
is skipped — those either have huge windows or aren't chat at all.

Modes (`KC_TRUNCATE_MODE` env)
------------------------------
- `preserve_first` (default): protect system + FIRST user-turn chunk
  (usually the task definition) + LAST user-turn chunk + everything after.
  Drop middle chunks oldest-first.
- `drop_oldest`: protect system + LAST user-turn chunk only. Drop other
  chunks oldest-first.

A "chunk" is a user message plus all following assistant/tool messages
until the next user — atomic so tool_call ↔ tool pairs and assistant
follow-ups never get orphaned at the new conversation head.

Output cap (max_tokens / max_completion_tokens)
-----------------------------------------------
LibreChat presets and LDR default to large values (e.g. 30000). vLLM has
two rejections that the cap must satisfy on *every* possible routing
target — the router selects deployment *after* this hook runs:

  * Static: `max_completion_tokens >= max_model_len` → 400.
  * Runtime: `input_tokens + max_completion_tokens > max_model_len` → 400.

Dynamic clamp:

  `output_cap = max(DEFAULT_OUTPUT_RESERVE,
                    min_ctx − input_tokens − OUTPUT_CAP_BUFFER)`

`min_ctx` is the smallest deployment's actual ctx (so any router pick is
safe). `OUTPUT_CAP_BUFFER` (env `KC_OUTPUT_CAP_BUFFER`, default 1024)
absorbs chat-template wrapper drift. `input_tokens` is counted via
`litellm.token_counter(model, messages, tools=…)` — tools are passed
explicitly because pre_call_check otherwise ignores them and tool-heavy
calls misroute. We clamp whichever field the caller used (legacy
`max_tokens` or newer OpenAI `max_completion_tokens`).

Stub-user inject
----------------
The local chat-template hard-rejects with "No user query found in messages"
if no message has `role: user`. LDR's ReAct follow-up calls sometimes look
like `[system, assistant(tool_calls), tool]` — no fresh user turn. We
append a short `{"role": "user", "content": "Continue."}` so the template
builds; semantically equivalent to "now respond" in agent contexts.

Sizing
------
`budget = ctx - output_reserve - SAFETY_MARGIN - notice_reserve`

- `output_reserve` = clamped output cap (see above).
- `SAFETY_MARGIN` (512) absorbs chat-template wrapper tokens + tokenizer
  drift (litellm uses cl100k_base for unknown models; Qwen is ~1-2% denser).
- `notice_reserve` reserves headroom for the system-msg block we attach
  after trimming. Larger when `SUMMARIZE_ENABLED` (room for the LLM
  summary) than the plain notice.

Optional LLM summary (`KC_TRUNCATE_SUMMARIZE=1`)
------------------------------------------------
When enabled, dropped chunks are condensed by a back-channel call to
`KC_TRUNCATE_SUMMARY_MODEL` (default `local/gemma-4-26b`) and the
result is folded into the leading system message inside an
`<earlier_conversation_summary>` block, framed as "facts already
established with the user — answer from them, don't invent beyond". That
framing counters the local chat model's tendency to treat any summary-shaped
block as prior-session memory. The summarizer call carries an `X-KC-No-Truncate`
header so this hook short-circuits and can't recurse. Failure or timeout
silently falls back to the plain notice so user latency isn't held
hostage to the summary subsystem.

When trim can't fit
-------------------
If no chunks are droppable, or if the protected chunks alone exceed budget,
we return a `ContextWindowExceededError` with a Korean user-facing message
— `_make_ctx_error()`. Letting the raw vLLM 400 propagate exposed English
provider stacks. LibreChat strips its own English wrappers when it sees
the `[KloudChat]` marker (see `scripts/librechat-patch.py` and
`rag-patches/patch_librechat_error_unwrap.js`).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Union

from litellm.integrations.custom_logger import CustomLogger

log = logging.getLogger("litellm-ctx-truncator")

CONFIG_PATH = os.environ.get("KC_TRUNCATE_CONFIG_PATH", "/app/config.yaml")

MODE = os.environ.get("KC_TRUNCATE_MODE", "preserve_first").strip().lower()
if MODE not in ("preserve_first", "drop_oldest"):
    log.warning("ctx-truncate: unknown KC_TRUNCATE_MODE=%r, falling back to preserve_first", MODE)
    MODE = "preserve_first"

SUMMARIZE_ENABLED = os.environ.get("KC_TRUNCATE_SUMMARIZE", "").strip() in ("1", "true", "yes")
SUMMARY_MODEL = os.environ.get("KC_TRUNCATE_SUMMARY_MODEL", "local/gemma-4-26b")
SUMMARY_TIMEOUT_SEC = float(os.environ.get("KC_TRUNCATE_SUMMARY_TIMEOUT_SEC", "30"))
SUMMARY_MAX_TOKENS = int(os.environ.get("KC_TRUNCATE_SUMMARY_MAX_TOKENS", "400"))
LITELLM_URL = os.environ.get("KC_TRUNCATE_LITELLM_URL", "http://localhost:8000")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

# Recursion guard. Summarizer call carries this header so we bail before
# attempting to truncate (and therefore can't trigger another summary call).
NO_TRUNCATE_HEADER = "x-kc-no-truncate"

DEFAULT_OUTPUT_RESERVE = 2048
# Chat-template wrappers + tokenizer drift (litellm uses cl100k_base for
# unknown models; Qwen actually tokenizes ~1-2% denser). 512 absorbs both.
SAFETY_MARGIN = 512
# Headroom for the notice/summary we'll attach to the leading system msg.
NOTICE_RESERVE = 150
# Worst-case for the LLM summary block (max_tokens + tag wrapper). Used
# instead of NOTICE_RESERVE when SUMMARIZE_ENABLED.
SUMMARY_RESERVE_OVERHEAD = 100  # added to SUMMARY_MAX_TOKENS for the reserve

# Output cap = min_ctx (smallest deployment in the model_name group) minus
# this buffer. vLLM rejects when max_completion_tokens >= max_model_len
# (static check, no input length involvement) — and the router can route a
# short-input request to any deployment, so the cap must clear the smallest
# deployment's limit. 1024 buffer keeps the cap strictly under and leaves
# room for chat-template wrappers.
OUTPUT_CAP_BUFFER = int(os.environ.get("KC_OUTPUT_CAP_BUFFER", "1024"))

CHAT_CALL_TYPES = frozenset({"completion", "acompletion"})

# Markers we plant in the leading system message when we modify it. If we
# see one on a follow-up invocation (retry, double-firing) we bail —
# trimming an already-trimmed message would stack notices/summaries and
# eat more context every pass.
TRIM_MARKERS = ("[KloudChat]", "<earlier_conversation_summary")


def _load_ctx_maps(path: str) -> tuple[dict[str, int], dict[str, int]]:
    """Walk model_list, return ({model_name: max ctx}, {model_name: min ctx}).

    Two maps because deployments with the same model_name can have different
    ctx (e.g. 16K + 128K node mix). Both are needed:

      * MAX is used as the trim budget — preserves most history when the
        router ends up picking the largest-fit deployment.
      * MIN is used for output clamps (max_tokens / max_completion_tokens) —
        the router may route any short-input request to *any* deployment,
        so the clamp must clear the smallest deployment's max_model_len
        (vLLM static-rejects when the output cap reaches max_model_len).

    Field precedence per deployment:
      1. `model_info.actual_ctx_tokens` — deployment's real ctx capacity
         (emitted alongside conservative `max_input_tokens` so router
         routing and callback trim can use different limits).
      2. `model_info.max_input_tokens` — fallback when the deployment
         declares only a conservative input cap."""
    try:
        import yaml
    except Exception:
        log.warning("ctx-truncate: PyYAML missing; truncation disabled")
        return {}, {}
    try:
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("ctx-truncate: %s not found; truncation disabled", path)
        return {}, {}
    except Exception:
        log.exception("ctx-truncate: failed to parse %s", path)
        return {}, {}

    out_max: dict[str, int] = {}
    out_min: dict[str, int] = {}
    for entry in (cfg.get("model_list") or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("model_name")
        if not isinstance(name, str):
            continue
        ctx: Optional[int] = None
        info = entry.get("model_info") or {}
        if isinstance(info, dict):
            v = info.get("actual_ctx_tokens")
            if isinstance(v, int) and v > 0:
                ctx = v
            else:
                v = info.get("max_input_tokens")
                if isinstance(v, int) and v > 0:
                    ctx = v
        if ctx is None:
            continue
        cur_max = out_max.get(name)
        out_max[name] = ctx if cur_max is None else max(cur_max, ctx)
        cur_min = out_min.get(name)
        out_min[name] = ctx if cur_min is None else min(cur_min, ctx)
    return out_max, out_min


_CTX_MAP, _CTX_MIN_MAP = _load_ctx_maps(CONFIG_PATH)
log.info(
    "ctx-truncate: loaded %d ctx-aware deployments (mode=%s, summarize=%s)",
    len(_CTX_MAP), MODE, SUMMARIZE_ENABLED,
)


def _estimate_tokens(model: str, messages: list[dict], tools: Optional[list] = None) -> int:
    """Count messages + tools as they will be rendered into the chat-template
    prompt. Some `token_counter` versions count only `messages` and omit `tools`
    schemas; long tool definitions then slip past pre_call_check + our budget
    calc, causing vLLM to reject the actual rendered prompt with
    ContextWindowExceededError. Pass tools explicitly if the installed
    token_counter accepts it; else add a coarse JSON char/4 estimate of the
    tools array on top of the messages-only count."""
    try:
        from litellm import token_counter
        try:
            return int(token_counter(model=model, messages=messages, tools=tools))
        except TypeError:
            base = int(token_counter(model=model, messages=messages))
            if tools:
                base += max(1, len(json.dumps(tools, ensure_ascii=False)) // 4)
            return base
    except Exception:
        try:
            payload = messages if not tools else {"m": messages, "t": tools}
            return max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)
        except Exception:
            return 0


def _chunk_messages(messages: list[dict]) -> list[tuple[int, int]]:
    """Group into atomic (start, end_exclusive) chunks: leading systems get a
    chunk each, then every user starts a new chunk that runs through all
    following assistant/tool messages until the next user. Splitting a
    chunk would orphan assistant follow-ups or tool_call ↔ tool pairs."""
    chunks: list[tuple[int, int]] = []
    n = len(messages)
    i = 0
    while i < n and messages[i].get("role") == "system":
        chunks.append((i, i + 1))
        i += 1
    while i < n:
        if messages[i].get("role") == "user":
            j = i + 1
            while j < n and messages[j].get("role") != "user":
                j += 1
            chunks.append((i, j))
            i = j
        else:
            chunks.append((i, i + 1))
            i += 1
    return chunks


def _last_user_chunk_index(messages: list[dict], chunks: list[tuple[int, int]]) -> Optional[int]:
    for ci in range(len(chunks) - 1, -1, -1):
        s, _ = chunks[ci]
        if messages[s].get("role") == "user":
            return ci
    return None


def _first_user_chunk_index(messages: list[dict], chunks: list[tuple[int, int]]) -> Optional[int]:
    for ci, (s, _e) in enumerate(chunks):
        if messages[s].get("role") == "user":
            return ci
    return None


def _materialize(messages: list[dict], chunks: list[tuple[int, int]], dropped: set[int]) -> list[dict]:
    return [
        messages[mi]
        for ci, (s, e) in enumerate(chunks)
        if ci not in dropped
        for mi in range(s, e)
    ]


def _select_drops(
    messages: list[dict], chunks: list[tuple[int, int]], model: str, budget: int, mode: str,
    tools: Optional[list] = None,
) -> tuple[set[int], list[int]]:
    """Decide which chunks to drop. Returns (dropped_set, drop_order_list).

    drop_order_list is in oldest-first order — used by the summarizer to
    preserve chronology when summarizing."""
    last_user_ci = _last_user_chunk_index(messages, chunks)
    if last_user_ci is None:
        return set(), []

    protected: set[int] = set()
    for ci, (s, _e) in enumerate(chunks):
        if messages[s].get("role") == "system":
            protected.add(ci)
        if ci >= last_user_ci:
            protected.add(ci)

    if mode == "preserve_first":
        first_user_ci = _first_user_chunk_index(messages, chunks)
        if first_user_ci is not None and first_user_ci != last_user_ci:
            protected.add(first_user_ci)

    droppable = [ci for ci in range(len(chunks)) if ci not in protected]
    if not droppable:
        return set(), []

    dropped: set[int] = set()
    order: list[int] = []
    for ci in droppable:
        kept = _materialize(messages, chunks, dropped | {ci})
        dropped.add(ci); order.append(ci)
        if _estimate_tokens(model, kept, tools=tools) <= budget:
            return dropped, order

    return dropped, order  # still over budget, but caller handles


def _notice_text(dropped: int, ctx_limit: int) -> str:
    return (
        f"\n\n[KloudChat] {dropped} earlier message group(s) were trimmed to "
        f"fit the {ctx_limit}-token context window for this model. Earlier "
        f"history is not visible to the model."
    )


def _make_ctx_error(model: str, ctx_limit: int, current_tokens: int) -> Exception:
    """User-facing Korean message when we can't make the request fit.

    Returned (not raised) from async_pre_call_hook; LiteLLM proxy raises it
    and the message lands in the 400 response body — LibreChat then renders
    that body inside its generic error wrapper. So the inner text needs to
    be self-contained: state the limit, the actual size, and the user's
    concrete options (shorter input / new chat / larger-ctx model)."""
    from litellm.exceptions import ContextWindowExceededError
    msg = (
        f"[KloudChat] 입력이 모델의 컨텍스트 한도({ctx_limit:,} 토큰)를 초과했습니다 "
        f"— 현재 약 {current_tokens:,} 토큰. "
        f"메시지를 줄이거나, 새 대화로 다시 시도해주세요."
    )
    return ContextWindowExceededError(
        message=msg,
        model=model,
        llm_provider="hosted_vllm",
    )


def _summary_text(summary: str) -> str:
    # Framing intent:
    #  - tell the model these bullets came from THIS conversation's earlier
    #    turns (so it answers from them, not from outside knowledge)
    #  - tell it not to invent details beyond what's stated (so a sparse
    #    summary doesn't get extrapolated into hallucinated prior sessions —
    #    the exact problem that librechat.yaml's summarization=false was
    #    avoiding)
    return (
        "\n\n<earlier_conversation_summary>\n"
        "The earlier turns of THIS conversation were trimmed for length. "
        "Below are condensed facts and topics from that history. "
        "Treat these as facts already established with the user — answer "
        "from them when relevant. Do not invent details beyond what is "
        "explicitly stated here.\n"
        "---\n"
        f"{summary.strip()}\n"
        "</earlier_conversation_summary>"
    )


def _attach_notice(messages: list[dict], note: str) -> None:
    """Fold a notice/summary into the leading system message in-place.
    Qwen / vLLM templates reject multiple consecutive `system` messages
    (`System message must be at the beginning`), so merge — don't insert."""
    if messages and messages[0].get("role") == "system":
        head = messages[0]
        existing = head.get("content")
        if isinstance(existing, str):
            head["content"] = existing + note
        elif isinstance(existing, list):
            head["content"] = existing + [{"type": "text", "text": note}]
        else:
            head["content"] = note.lstrip()
    else:
        messages.insert(0, {"role": "system", "content": note.lstrip()})


def _summarizer_input_budget_chars() -> int:
    """Char cap for the transcript we feed to the summarizer model.

    Derived from the summarizer's own ctx (from _CTX_MAP) minus reserves
    for the system prompt + requested max_tokens output + safety margin.
    Char/4 ≈ token, used coarsely. Falls back to a conservative 16K chars
    (~4K tokens) if the summarizer isn't in the ctx map."""
    sum_ctx = _CTX_MAP.get(SUMMARY_MODEL)
    if not sum_ctx:
        return 16_000
    sys_prompt_reserve = 300        # ~75 tokens for the summarizer's system msg
    budget_tokens = sum_ctx - SUMMARY_MAX_TOKENS - sys_prompt_reserve - SAFETY_MARGIN
    if budget_tokens <= 0:
        return 16_000
    # Heuristic: chars/token ≈ 4 for English/code, ≈ 2 for CJK. Use 3 to
    # split the difference and stay safely under ctx for mixed content.
    return max(2_000, budget_tokens * 3)


def _serialize_chunks_for_summary(messages: list[dict], chunks: list[tuple[int, int]], dropped_order: list[int]) -> str:
    """Render dropped chunks chronologically as a transcript, bounded to fit
    the summarizer's own ctx.

    When the full transcript exceeds the summarizer budget, keep both ends
    and elide the middle. Personal facts (names, preferences) tend to land
    in the EARLIEST dropped turns — the user often establishes context up
    front, then chats. Recent dropped turns also matter because they're
    adjacent to the live conversation. The middle is usually mid-topic
    filler, which compresses best by being summarized as "discussed N
    intermediate topics" rather than verbatim. So we split the budget
    60/40 (front/back) and drop the middle."""
    max_chars = _summarizer_input_budget_chars()
    lines: list[str] = []
    for ci in dropped_order:
        s, e = chunks[ci]
        for mi in range(s, e):
            m = messages[mi]
            role = m.get("role", "?")
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False) if content is not None else ""
            lines.append(f"{role}: {content}")

    total = sum(len(x) for x in lines) + len(lines)  # +newlines
    if total <= max_chars:
        return "\n".join(lines)

    front_budget = int(max_chars * 0.6)
    back_budget = max_chars - front_budget - 80  # reserve for ellipsis line

    front: list[str] = []
    fr = 0
    fi = 0
    for line in lines:
        if fr + len(line) + 1 > front_budget:
            break
        front.append(line)
        fr += len(line) + 1
        fi += 1

    back: list[str] = []
    br = 0
    bi = len(lines)
    for line in reversed(lines[fi:]):
        if br + len(line) + 1 > back_budget:
            break
        back.append(line)
        br += len(line) + 1
        bi -= 1
    back.reverse()

    middle_count = bi - fi
    if middle_count <= 0:
        # Front+back already covers everything (rare with the 0.6/0.4 split).
        return "\n".join(front + back)
    return "\n".join(front + [f"... [{middle_count} middle lines elided to fit summarizer ctx]"] + back)


async def _summarize(transcript: str) -> Optional[str]:
    """Back-channel LLM call. Returns summary text or None on any failure
    (caller falls back to plain notice — no user-visible error)."""
    if not LITELLM_KEY:
        log.warning("ctx-truncate: LITELLM_MASTER_KEY empty, can't summarize")
        return None
    try:
        import httpx
    except Exception:
        log.warning("ctx-truncate: httpx not installed, can't summarize")
        return None

    sys_prompt = (
        "You compress a chat-conversation excerpt for later reference by the "
        "same assistant. Output dense bullets capturing, with highest "
        "priority: (1) specific facts the user shared about themselves "
        "(names, preferences, identifiers, file paths, decisions), "
        "(2) the user's task or topic, (3) any pending questions. Then "
        "general topics discussed. Quote names and proper nouns verbatim "
        "(e.g. write the actual color / pet name / project name). Be "
        "concise. Do not invent details. Reply with bullets only — no "
        "preamble or closing line."
    )
    body = {
        "model": SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": transcript},
        ],
        "max_tokens": SUMMARY_MAX_TOKENS,
        "temperature": 0,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LITELLM_KEY}",
        # Recursion guard: this hook bails if it sees this header.
        NO_TRUNCATE_HEADER: "1",
    }
    try:
        async with httpx.AsyncClient(timeout=SUMMARY_TIMEOUT_SEC) as c:
            r = await c.post(f"{LITELLM_URL}/v1/chat/completions", json=body, headers=headers)
        if r.status_code >= 400:
            log.warning("ctx-truncate summarizer HTTP %d: %s", r.status_code, r.text[:300])
            return None
        data = r.json()
        text = data["choices"][0]["message"].get("content")
        if not isinstance(text, str) or not text.strip():
            log.warning("ctx-truncate summarizer returned empty content")
            return None
        return text
    except Exception:
        log.exception("ctx-truncate summarizer call failed")
        return None


def _request_has_no_truncate_marker(data: dict) -> bool:
    """Honor the recursion-guard header. LiteLLM forwards client headers
    into `data["headers"]` (lowercased keys) for proxy requests."""
    headers = data.get("headers")
    if isinstance(headers, dict):
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == NO_TRUNCATE_HEADER:
                return bool(v)
    # Defensive: also check proxy_server_request shape some versions use.
    psr = data.get("proxy_server_request")
    if isinstance(psr, dict):
        h = psr.get("headers")
        if isinstance(h, dict):
            for k, v in h.items():
                if isinstance(k, str) and k.lower() == NO_TRUNCATE_HEADER:
                    return bool(v)
    return False


class CtxTruncator(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Optional[Union[Exception, str, dict]]:
        try:
            if call_type not in CHAT_CALL_TYPES:
                return None
            if not isinstance(data, dict):
                return None
            if _request_has_no_truncate_marker(data):
                return None
            model = data.get("model")
            ctx_limit = _CTX_MAP.get(model) if isinstance(model, str) else None
            if not ctx_limit:
                return None
            messages = data.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            # tools schema is part of the rendered chat-template prompt — count
            # it so budget reflects what vLLM will actually see (see
            # _estimate_tokens docstring).
            tools = data.get("tools") if isinstance(data.get("tools"), list) else None

            # Idempotency: if a previous pass already added our notice or
            # summary to the leading system message, don't re-trim. Without
            # this, a LiteLLM router retry (e.g. after a token count nudge
            # past the deployment max) would invoke this hook again on the
            # already-modified messages and stack notices/summaries.
            head_content = ""
            if messages[0].get("role") == "system":
                hc = messages[0].get("content")
                if isinstance(hc, str):
                    head_content = hc
                elif isinstance(hc, list):
                    head_content = " ".join(
                        p.get("text", "") for p in hc if isinstance(p, dict)
                    )
            if any(marker in head_content for marker in TRIM_MARKERS):
                return None

            # Clamp absurd max_tokens / max_completion_tokens. vLLM rejects when
            # output_cap >= max_model_len (static) *and* when input+output >
            # max_model_len (runtime). Both checks live on the smallest
            # deployment the router might pick, so cap = min_ctx - input -
            # buffer. LDR / deep-research default to 30000 — fits 128K but
            # blows up on 16K; this dynamic cap keeps every routing target
            # safe regardless of input length. Both `max_tokens` (legacy) and
            # `max_completion_tokens` (newer OpenAI) map to the same
            # semantics — clamp whichever the caller used.
            clamp_ctx = _CTX_MIN_MAP.get(model, ctx_limit) if isinstance(model, str) else ctx_limit
            input_tokens = _estimate_tokens(model, messages, tools=tools)
            output_cap = max(DEFAULT_OUTPUT_RESERVE, clamp_ctx - input_tokens - OUTPUT_CAP_BUFFER)
            mt_field = "max_tokens" if "max_tokens" in data else (
                "max_completion_tokens" if "max_completion_tokens" in data else None
            )
            requested_max = data.get(mt_field) if mt_field else None
            if isinstance(requested_max, int) and requested_max > output_cap:
                log.warning(
                    "ctx-truncate: model=%s clamping %s %d → %d (min_ctx=%d input=%d buffer=%d)",
                    model, mt_field, requested_max, output_cap, clamp_ctx, input_tokens, OUTPUT_CAP_BUFFER,
                )
                data[mt_field] = output_cap
                reserve = output_cap
            elif isinstance(requested_max, int) and requested_max > 0:
                reserve = requested_max
            else:
                reserve = DEFAULT_OUTPUT_RESERVE

            # Stub-user gate: the local chat-template hard-rejects messages with
            # no user role ("No user query found in messages"). LDR's ReAct
            # loop occasionally sends [system, assistant(tool_calls), tool]
            # follow-ups without a fresh user turn. Inject a short placeholder
            # so the template builds — semantically equivalent to "now respond
            # to the above" in agent contexts.
            if not any(m.get("role") == "user" for m in messages):
                log.warning(
                    "ctx-truncate: model=%s no user role in %d messages — injecting stub",
                    model, len(messages),
                )
                messages = messages + [{"role": "user", "content": "Continue."}]
                data["messages"] = messages
            notice_reserve = (
                SUMMARY_MAX_TOKENS + SUMMARY_RESERVE_OVERHEAD
                if SUMMARIZE_ENABLED else NOTICE_RESERVE
            )
            budget = ctx_limit - reserve - SAFETY_MARGIN - notice_reserve
            if budget <= 0:
                # Even with the clamp, reserves consume the whole ctx — degenerate
                # config. Surface a clear Korean error instead of letting vLLM
                # return its English BadRequestError.
                current = _estimate_tokens(model, messages, tools=tools)
                return _make_ctx_error(model, ctx_limit, current)

            current = _estimate_tokens(model, messages, tools=tools)
            if current <= budget:
                return None

            chunks = _chunk_messages(messages)
            dropped_set, drop_order = _select_drops(messages, chunks, model, budget, MODE, tools=tools)
            if not dropped_set:
                # Nothing droppable (single user turn, or every chunk is protected)
                # and we're still over budget. Don't let the raw vLLM error reach
                # the user — return a friendly Korean ctx-exceeded error.
                log.warning(
                    "ctx-truncate: model=%s no droppable chunks (tokens=%d budget=%d chunks=%d) — returning friendly error",
                    model, current, budget, len(chunks),
                )
                return _make_ctx_error(model, ctx_limit, current)

            trimmed = _materialize(messages, chunks, dropped_set)
            if _estimate_tokens(model, trimmed, tools=tools) > budget:
                # Couldn't fit even after dropping every droppable — protected
                # chunks (system + first/last user) alone exceed budget. Friendly
                # error instead of letting vLLM's English 400 hit the user.
                log.warning(
                    "ctx-truncate: model=%s could not fit (tokens=%d budget=%d after dropping %d/%d chunks) — returning friendly error",
                    model, current, budget, len(dropped_set), len(chunks),
                )
                return _make_ctx_error(model, ctx_limit, current)

            summary: Optional[str] = None
            if SUMMARIZE_ENABLED:
                transcript = _serialize_chunks_for_summary(messages, chunks, drop_order)
                summary = await _summarize(transcript)

            if summary:
                _attach_notice(trimmed, _summary_text(summary))
            else:
                _attach_notice(trimmed, _notice_text(len(dropped_set), ctx_limit))

            new_count = _estimate_tokens(model, trimmed, tools=tools)
            log.warning(
                "ctx-truncate: model=%s mode=%s ctx=%d budget=%d tokens=%d→%d dropped=%d/%d chunks summary=%s",
                model, MODE, ctx_limit, budget, current, new_count,
                len(dropped_set), len(chunks), "yes" if summary else "no",
            )
            data["messages"] = trimmed
            return data
        except Exception:
            log.exception("ctx truncator: pre_call hook failed; passing through")
            return None


truncator_instance = CtxTruncator()


# Mirror sanitize_python_tag / inject_super_agent_user self-register pattern.
try:
    import litellm as _litellm
    if truncator_instance not in _litellm.callbacks:
        _litellm.callbacks.append(truncator_instance)
except Exception:
    log.exception("ctx truncator: self-register failed")
