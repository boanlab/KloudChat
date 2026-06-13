"""Super-agent OpenAI-compatible shim.

Single-stage tool-aware front for KloudChat workflows. The shim forwards
chat completions to a chat model via LiteLLM, forwarding any `tools` /
`tool_choice` so LibreChat's tool loop works as usual. By default that model is
CHAT_MODEL (gemma-4-26b) — a single brain for chat, tool-call orchestration,
vision, and artifacts that emits clean tool_calls on its own (no separate
tool-call router needed). The per-request model is chosen in `_select_chat_model`.

Heavy routing (optional)
------------------------
When ROUTE_HEAVY_MODEL is set and the request is NOT in artifact mode, the
`_is_heavy()` heuristic (explicit deep-reasoning keyword in the latest user
message, OR total user/tool input >= ROUTE_HEAVY_MIN_CHARS) promotes the request
to ROUTE_HEAVY_MODEL (e.g. 122b) instead of CHAT_MODEL. Empty => off. Precedence:
ARTIFACT_MODEL override > ROUTE_HEAVY_MODEL > CHAT_MODEL.

Artifacts (single-pass)
-----------------------
LibreChat's "artifacts" toggle injects its built-in :::artifact guide into the
system prompt. When that toggle is on we:
  1. Force `tool_choice="none"` from the first turn so the model emits the
     artifact in one shot instead of looping on web_search/execute_code (which
     degenerates into empty-code 400s and never produces the artifact).
  2. Detox the system message — replace LibreChat's long artifact prompt block
     with a CONCISE artifact directive (`_CONCISE_ARTIFACT_DIRECTIVE`). The
     verbose 11K-token artifact prompt makes the model degenerate (emit just
     '```' and stop); the concise directive yields full, well-designed output.
  3. Call gemma non-stream (so we can post-process), then `_post_process_artifact`
     guarantees a well-formed :::artifact wrapper around the first renderable
     code block.
This is a single gemma pass — no separate coder model, no draft+refine.

Behaviour notes:
  - On chat HTTP error or timeout we return an OpenAI-shaped error envelope so
    the client renders the cause instead of a generic upstream failure.
  - Placement awareness: a TTL-cached probe of LiteLLM's /v1/models lets us
    short-circuit when CHAT_MODEL isn't deployed — we return an OpenAI-shaped
    error envelope naming the models that *are* live. Probe failure falls back
    to a best-effort attempt (try, surface errors as they come).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

LITELLM_URL = (os.getenv("LITELLM_URL") or "http://litellm:8000").rstrip("/")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "local/gemma-4-26b")

# Artifact (code / slide / webpage generation) chat model OVERRIDE. Normally
# empty: artifacts are served by CHAT_MODEL (gemma-4-26b) on a single pass with
# the concise artifact directive (see module docstring). If set AND the request
# is in artifact mode (LibreChat artifacts toggle on → :::artifact guide injected
# into the system prompt, detected by _artifact_toggle_on), the shim routes to
# this model instead. Empty ⇒ feature off (always CHAT_MODEL).
ARTIFACT_MODEL = os.environ.get("ARTIFACT_MODEL", "")

# Artifact requests get a generous token floor — gemma's rich, fully-designed
# slide decks / pages run long (20K+ chars) and truncate at typical 2-4K caps.
ARTIFACT_MAX_TOKENS = int(os.environ.get("ARTIFACT_MAX_TOKENS", "8000"))

# Difficulty-based routing (heuristic). When ROUTE_HEAVY_MODEL is set, a request
# the heuristic judges "heavy" (explicit deep-reasoning intent OR large user
# input) is routed to that model (e.g. local/qwen3.5:122b) instead of CHAT_MODEL
# (gemma). Empty ⇒ off (always CHAT_MODEL). Artifact mode is excluded — artifacts
# stay on the gemma-tuned single-pass path (ARTIFACT_MODEL handles that override).
ROUTE_HEAVY_MODEL = os.environ.get("ROUTE_HEAVY_MODEL", "")
# Total chars across user/tool messages above which a request counts as heavy
# (long pasted docs / RAG context / multi-turn). System+assistant excluded so the
# big agent system prompt doesn't trip it.
ROUTE_HEAVY_MIN_CHARS = int(os.environ.get("ROUTE_HEAVY_MIN_CHARS", "4000"))
# Explicit "think hard" intent in the latest user message → heavy regardless of length.
_HEAVY_KW_RE = re.compile(
    r"심층|깊이\s*있게|상세\s*분석|면밀|철저(?:히|하게)|비교\s*분석|단계별|차근차근|증명|도출|논증|"
    r"아키텍처|architecture|trade[\s-]?off|"
    r"deep[\s-]?dive|thorough|rigorous|step[\s-]by[\s-]step|prove|derive|reason\s+through",
    re.IGNORECASE,
)


CHAT_TIMEOUT_SEC = float(os.environ.get("CHAT_TIMEOUT_SEC", "600"))
# How long a successful /v1/models probe is trusted before re-fetching. The
# scheduler reconverges on the order of minutes, so 30s is short enough to
# notice placement changes quickly and long enough to amortize probe cost
# across bursts of requests.
MODELS_PROBE_TTL_SEC = float(os.environ.get("MODELS_PROBE_TTL_SEC", "30"))
MODELS_PROBE_TIMEOUT_SEC = float(os.environ.get("MODELS_PROBE_TIMEOUT_SEC", "5"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("super-agent")

app = FastAPI()


def _auth_headers() -> dict[str, str]:
    """Master-key auth — used for all downstream calls back into LiteLLM.

    Cost attribution to the original caller happens via end_user tracking:
    LiteLLM's pre_call hook (litellm-callbacks/inject_super_agent_user.py)
    injects `extra_body.user = caller_user_id` for local/auto-route requests,
    so the shim receives `body.user = caller_id` and the `{**body, ...}` spread
    in the chat call site propagates it. Downstream LiteLLM then tags the
    spend with end_user=caller — queryable via /customer/daily/activity."""
    return {"Authorization": f"Bearer {LITELLM_API_KEY}"} if LITELLM_API_KEY else {}


# Cached snapshot of what LiteLLM currently exposes via /v1/models. None means
# "we haven't been able to probe successfully yet" — treat as unknown and fall
# back to a best-effort attempt (try chat, surface errors as they come). A set
# means "this is the authoritative list of deployed model_names right now" and
# we can short-circuit a missing chat model.
_deployed_models: set[str] | None = None
_deployed_models_at: float = 0.0
_deployed_models_lock = asyncio.Lock()


async def _probe_deployed_models() -> set[str] | None:
    """Fetch the deployed model_name set from LiteLLM. Returns None on failure
    so callers can distinguish 'probe failed' from 'probe succeeded, model
    absent' — the former falls back to a best-effort attempt, the latter
    triggers the missing-chat-model short-circuit."""
    try:
        async with httpx.AsyncClient(timeout=MODELS_PROBE_TIMEOUT_SEC) as c:
            r = await c.get(
                f"{LITELLM_URL}/v1/models",
                headers=_auth_headers(),
            )
        if r.status_code >= 400:
            LOG.warning("models probe HTTP %d: %s", r.status_code, r.text[:200])
            return None
        data = r.json().get("data") or []
        return {m.get("id") for m in data if isinstance(m, dict) and m.get("id")}
    except Exception as e:
        LOG.warning("models probe exception: %r", e)
        return None


async def _get_deployed_models() -> set[str] | None:
    """Return cached deployed-model set, refreshing past TTL under a lock so
    a request burst issues only one upstream probe. On refresh failure we
    keep the prior cache (stale-but-better-than-nothing); only return None
    if we've never had a successful probe."""
    global _deployed_models, _deployed_models_at
    now = time.monotonic()
    if _deployed_models is not None and (now - _deployed_models_at) < MODELS_PROBE_TTL_SEC:
        return _deployed_models
    async with _deployed_models_lock:
        # Re-check inside the lock — another coroutine may have refreshed.
        now = time.monotonic()
        if _deployed_models is not None and (now - _deployed_models_at) < MODELS_PROBE_TTL_SEC:
            return _deployed_models
        probed = await _probe_deployed_models()
        if probed is not None:
            _deployed_models = probed
            _deployed_models_at = now
            LOG.info("models probe ok: %d models (chat_present=%s)",
                     len(probed),
                     CHAT_MODEL in probed)
        return _deployed_models


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "chat": CHAT_MODEL,
            "artifact": ARTIFACT_MODEL or None,
            "heavy": ROUTE_HEAVY_MODEL or None}


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    # Minimal OpenAI-shape models list so LiteLLM/clients don't 404 on probe.
    return {
        "object": "list",
        "data": [
            {"id": "super-agent", "object": "model",
             "created": int(time.time()), "owned_by": "kloudchat"}
        ],
    }


def _error_sse_chunks(message: str) -> list[bytes]:
    """OpenAI-shaped SSE error: one assistant chunk with `content`, one final
    chunk with `finish_reason="stop"`, then [DONE]. LibreChat / langchain
    parsers dereference `choices[0].delta.role`; a bare `{error: ...}` chunk
    (no `choices`) crashes them with "cannot read properties of undefined
    (reading 'role')". This shape renders the upstream error text in the
    chat surface instead."""
    base = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": CHAT_MODEL,
    }
    first = {**base, "choices": [{
        "index": 0,
        "delta": {"role": "assistant", "content": message},
        "finish_reason": None,
    }]}
    last = {**base, "choices": [{
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }]}
    return [
        f"data: {json.dumps(first)}\n\n".encode(),
        f"data: {json.dumps(last)}\n\n".encode(),
        b"data: [DONE]\n\n",
    ]


# 토큰 반복 degeneration(`a a a a a`) 억제 — 샘플링에 penalty 없으면 모델이 한 토큰에
# 갇혀 반복한다. frequency_penalty 는 OpenAI 표준(litellm forward 확실), repetition_penalty
# 는 vLLM 전용(곱셈식, 심한 반복에 강함). 0/1.0 = 비활성.
# 주의: 너무 크면(예: freq 0.5 + rep 1.2) 코드의 정상적 토큰 반복까지 억눌러 모델이
# 조기 종료(opener 만 내고 멈춤)한다. 약하게 — frequency 만 mild, repetition 은 기본 off.
CHAT_FREQUENCY_PENALTY = float(os.environ.get("CHAT_FREQUENCY_PENALTY", "0.3"))
CHAT_REPETITION_PENALTY = float(os.environ.get("CHAT_REPETITION_PENALTY", "1.0"))


def _apply_antirepeat(chat_body: dict[str, Any]) -> None:
    """반복 degeneration 억제 샘플링 penalty 주입(요청에 이미 있으면 존중)."""
    if CHAT_FREQUENCY_PENALTY and "frequency_penalty" not in chat_body:
        chat_body["frequency_penalty"] = CHAT_FREQUENCY_PENALTY
    if CHAT_REPETITION_PENALTY and CHAT_REPETITION_PENALTY != 1.0 \
            and "repetition_penalty" not in chat_body:
        chat_body["repetition_penalty"] = CHAT_REPETITION_PENALTY


async def _stream_chat(body: dict[str, Any], chat_model: str = CHAT_MODEL) -> AsyncIterator[bytes]:
    """Stream the chat model's response back to the client as raw SSE bytes."""
    chat_body = {**body, "model": chat_model, "stream": True}
    _apply_antirepeat(chat_body)
    LOG.info("chat → %s (stream)", chat_model)
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT_SEC) as c:
            async with c.stream(
                "POST",
                f"{LITELLM_URL}/v1/chat/completions",
                json=chat_body,
                headers={"Content-Type": "application/json", **_auth_headers()},
            ) as r:
                if r.status_code >= 400:
                    err = await r.aread()
                    err_text = err.decode("utf-8", "ignore")[:400]
                    LOG.error("chat HTTP %d: %s", r.status_code, err_text)
                    for sse in _error_sse_chunks(
                        f"[super-agent] upstream chat error ({r.status_code}): {err_text}"
                    ):
                        yield sse
                    return
                async for chunk in r.aiter_raw():
                    if chunk:
                        yield chunk
    except Exception as e:
        LOG.exception("chat stream exception")
        for sse in _error_sse_chunks(f"[super-agent] upstream chat exception: {e!r}"):
            yield sse


def _error_completion(message: str) -> dict[str, Any]:
    """OpenAI-shaped non-stream error envelope. Returned in place of a 500 so
    LibreChat sees a parseable `choices[0].message.role`/`.content` and can
    render the failure instead of throwing a property-access error."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": CHAT_MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": message},
            "finish_reason": "stop",
        }],
    }


async def _call_chat_nonstream(body: dict[str, Any], chat_model: str = CHAT_MODEL,
                               artifact_mode: bool = False) -> dict[str, Any]:
    """Non-streaming call to the chat model. Used when client didn't request stream."""
    chat_body = {**body, "model": chat_model, "stream": False}
    _apply_antirepeat(chat_body)
    LOG.info("chat → %s (non-stream)", chat_model)
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT_SEC) as c:
            r = await c.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json=chat_body,
                headers={"Content-Type": "application/json", **_auth_headers()},
            )
        if r.status_code >= 400:
            err_text = r.text[:400]
            LOG.error("chat HTTP %d: %s", r.status_code, err_text)
            return _error_completion(
                f"[super-agent] upstream chat error ({r.status_code}): {err_text}"
            )
        resp = r.json()
        _post_process_artifact(resp, body, artifact_mode=artifact_mode)
        return resp
    except Exception as e:
        LOG.exception("chat non-stream exception")
        return _error_completion(f"[super-agent] upstream chat exception: {e!r}")


# 사용자가 *artifact 형식* 으로 응답받을 의도였음을 추론하는 키워드. instruction 으로
# 100% 보장 안 되니 코드에서 후처리 — 모델이 raw 만 emit 한 경우 :::artifact{} 로 wrap.
_HTML_TRIGGER_PAT = re.compile(
    r"슬라이드|slide|프레젠테이션|presentation|발표\s*자료|강연\s*자료|강의\s*자료|"
    r"\bppt\b|\bdeck\b|"
    r"HTML\s*페이지|landing\s*page|웹\s*페이지|랜딩\s*페이지|랜딩\s*pages?|"
    r"웹\s*앱|web\s*app|단일\s*HTML|standalone\s*HTML|single[\s-]*(?:file\s*)?HTML",
    re.IGNORECASE,
)
_MERMAID_TRIGGER_PAT = re.compile(
    r"다이어그램|diagram|플로우|flow\s*chart|순서도|흐름도|시퀀스|sequence|"
    r"ERD|UML|클래스도|상태도|마인드맵|mind\s*map|gantt",
    re.IGNORECASE,
)
_MERMAID_FENCE_PAT = re.compile(
    r"```mermaid\s*\n(.*?)\n?```", re.DOTALL | re.IGNORECASE
)

# 모델이 슬라이드/HTML 에 data:base64 이미지를 박는데(지시로 금지해도 무시) 대개 깨진
# garbage SVG 라 broken-image 로 뜬다 → 아티팩트 본문에서 결정적으로 제거한다.
_DATA_IMG_RE = re.compile(
    r'<img\b[^>]*\bsrc\s*=\s*["\']data:[^"\']*["\'][^>]*>', re.IGNORECASE)


def _strip_data_images(s: str) -> str:
    return _DATA_IMG_RE.sub("", s)

# 펜스 언어 → LibreChat artifact MIME type. 모델은 :::artifact 래퍼를 일관되게 안 따르고
# ```lang 코드블록만 내놓는 경우가 많다(특히 한국어/일반 "앱" 요청). 토글이 켜져 있으면
# 첫 *렌더 가능* 블록을 그 펜스 언어로 추론해 감싼다.
_FENCE_ARTIFACT_TYPE = {
    "html": "text/html", "htm": "text/html",
    "jsx": "application/vnd.react", "tsx": "application/vnd.react",
    "react": "application/vnd.react", "javascript": "application/vnd.react", "js": "application/vnd.react",
    "mermaid": "application/vnd.mermaid", "svg": "image/svg+xml",
}
_ANY_FENCE_RE = re.compile(r"```([a-zA-Z0-9+_.-]*)[ \t]*\n(.*?)\n?```", re.DOTALL)

# LibreChat 의 11K 아티팩트 시스템 프롬프트와 만나면 모델이 광범위하게 degenerate 한다 —
# 슬라이드/웹페이지/대시보드/랜딩/게임/에디터 등에서 '```' 만 출력하고 멈춤(raw vLLM
# 에서도 동일하므로 콜백 무관, temperature/penalty 무관, 결정적). 같은 요청을
# 간결한 지시로 주면 정상 생성(8~10K자). post-process 가 :::artifact 래핑을 보장하므로
# 모델엔 "완전한 코드를 한 블록에" 만 알려주면 충분하다. artifact 경로에서만 치환한다.
_ARTIFACT_PROMPT_START = "The assistant can create and reference artifacts"
_CONCISE_ARTIFACT_DIRECTIVE = (
    "When asked to build something visual — webpage, web app, dashboard, slide deck / 발표자료, "
    "game, chart, SVG, or diagram — output a COMPLETE, self-contained artifact as CODE in a SINGLE "
    "fenced block: ```html for anything visual (a slide deck is ONE self-contained HTML page with "
    "CSS-styled slides, never markdown), ```jsx for React, ```svg, or ```mermaid. No prose, no "
    "bullet lists, no python — only the code block. Make it substantial and the design polished: "
    "cohesive color theme (2-3 accents, gradients ok), clear typography, generous spacing, rounded "
    "corners, subtle shadows. "
    "FOR SLIDE DECKS: a real navigable 16:9 deck — each slide a centered card (max-width ~960px, "
    "aspect-ratio:16/9, ~48px padding, heading + 3-5 substantive bullets, consistent theme). "
    "Progressive enhancement: by default (no JS) slides stack; an inline <script> adds a class to "
    "<body> and CSS hides non-current slides under it (body.js-on .slide:not(.active){display:none}). "
    "Add a fixed bottom bar with Prev (◀)/Next (▶) + 'current / total', wired to the buttons and "
    "ArrowLeft/ArrowRight/Space; first slide .active. "
    "Inline <style>/<script> only — NO external/base64 images, NO CDN. Build it fully from your own "
    "knowledge; output only the code block."
)


def _detox_artifact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """system 메시지의 LibreChat 아티팩트 프롬프트 블록을 간결한 지시로 치환한다(artifact
    경로 전용). 아티팩트 프롬프트는 base 프롬프트 뒤에 append 되므로 그 시작 마커부터
    끝까지(=ARTIFACT_GUIDE 포함) 잘라내고 간결 지시로 대체한다. 마커 없으면 무변경."""
    out: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if (isinstance(m, dict) and m.get("role") == "system" and isinstance(c, str)
                and _ARTIFACT_PROMPT_START in c):
            base = c[:c.find(_ARTIFACT_PROMPT_START)].rstrip()
            out.append({**m, "content": (base + ("\n\n" if base else "")) + _CONCISE_ARTIFACT_DIRECTIVE})
        else:
            out.append(m)
    return out


def _wrap_first_renderable_block(content: str) -> str | None:
    """첫 *렌더 가능* 코드펜스를 :::artifact 로 감싼다. type 은 펜스 언어(또는 bare
    펜스면 본문)로 추론. 내부 펜스는 bare ``` 로 정규화한다(LibreChat react/html
    파서가 언어 토큰 없는 펜스를 기대 — type 속성이 언어를 지정). 렌더 불가(python/bash
    등 평범한 코드)면 None → 코드블록 그대로 둔다."""
    m = _ANY_FENCE_RE.search(content)
    if m:
        lang, body, start, end = (m.group(1) or "").strip().lower(), m.group(2), m.start(), m.end()
    else:
        # 닫는 ``` 없음 — 큰 아티팩트가 max_tokens 에서 잘린 경우(```html ... <EOF>).
        # 여는 펜스부터 끝까지를 본문으로 보고 감싼다(잘린 코드라도 아티팩트로 렌더).
        mo = re.search(r"```([a-zA-Z0-9+_.-]*)[ \t]*\n(.*)$", content, re.DOTALL)
        if not mo:
            return None
        lang, body, start, end = (mo.group(1) or "").strip().lower(), mo.group(2), mo.start(), len(content)
    art_type = _FENCE_ARTIFACT_TYPE.get(lang)
    if art_type is None:
        if not lang:  # bare 펜스 — 본문으로 sniff
            if re.search(r"<!DOCTYPE|<html\b", body, re.IGNORECASE):
                art_type = "text/html"
            elif "import React" in body or re.search(r"export\s+default", body):
                art_type = "application/vnd.react"
        if art_type is None:
            return None  # 알 수 없는/평범한 코드 — 래핑 안 함
    if art_type == "text/html":
        body = _strip_data_images(body)
    identifier = f"artifact-{uuid.uuid4().hex[:8]}"
    wrapped = (f':::artifact{{identifier="{identifier}" type="{art_type}" title="Artifact"}}\n'
               f'```\n{body.rstrip()}\n```\n:::')
    return content[:start] + wrapped + content[end:]


# 모델이 자체적으로 (잘못된) :::artifact 래퍼를 emit 하는 경우 — Super Agent 의 도구 중심
# base 프롬프트 영향으로 `:::artifact{type:"html" filename=...}` 처럼 LibreChat 포맷이
# 아닌 opener(콜론/잘못된 type/identifier 없음)나 내부 ``` 펜스 없는 raw HTML 를 내놓기도
# 한다. LibreChat 파서는 `type="<MIME>"` + ``` 펜스를 요구하므로 렌더 안 된다. well-formed
# 면 그대로, malformed 면 정규화한다.
_WELLFORMED_ART_RE = re.compile(
    r':::artifact\{[^}]*type="[\w.+-]+/[\w.+-]+"[^}]*\}[^\n]*\n[ \t]*```', re.IGNORECASE)
_ART_OPENER_RE = re.compile(r':::artifact\{([^}]*)\}[^\n]*\n', re.IGNORECASE)
_TYPE_ATTR_RE = re.compile(r'type\s*[:=]\s*"?([\w./+-]+)"?', re.IGNORECASE)


def _normalize_artifact(content: str) -> str | None:
    """모델이 emit 한 malformed :::artifact 를 표준 포맷으로 재작성. opener 의 type
    (콜론/등호 무관)을 MIME 으로 매핑하고, 본문의 leading/trailing ``` 펜스를 정리한 뒤
    `identifier`/`type="MIME"`/bare ``` 펜스로 감싼다. 앞쪽 prose 는 보존, 닫는 ::: 가
    없거나 truncated 여도 끝까지 본문으로 본다."""
    m = _ART_OPENER_RE.search(content)
    if not m:
        return None
    pre = content[:m.start()]
    tm = _TYPE_ATTR_RE.search(m.group(1))
    raw = tm.group(1).lower() if tm else ""
    art_type = _FENCE_ARTIFACT_TYPE.get(raw) or (raw if "/" in raw else None)
    after = content[m.end():]
    close = after.find("\n:::")
    body = after[:close] if close >= 0 else after
    body = re.sub(r'^\s*```[a-zA-Z0-9+_.-]*[ \t]*\n', '', body)   # leading fence 제거
    body = re.sub(r'\n```\s*$', '', body.strip())                  # trailing fence 제거
    if art_type is None:
        if re.search(r'<!DOCTYPE|<html\b', body, re.IGNORECASE):
            art_type = "text/html"
        elif "import React" in body or re.search(r'export\s+default', body):
            art_type = "application/vnd.react"
        else:
            art_type = "text/html"
    if art_type == "text/html":
        body = _strip_data_images(body)
    identifier = f"artifact-{uuid.uuid4().hex[:8]}"
    wrapped = (f':::artifact{{identifier="{identifier}" type="{art_type}" title="Artifact"}}\n'
               f'```\n{body.strip()}\n```\n:::')
    return pre.rstrip() + ("\n\n" if pre.strip() else "") + wrapped


def _post_process_artifact(resp: dict[str, Any], req_body: dict[str, Any],
                           artifact_mode: bool = False) -> None:
    """모델이 artifact wrapper 없이 raw HTML 또는 ```mermaid 만 emit 한 경우
    :::artifact{} 로 감싼다. 조건 (모두 만족):
      - 사용자 마지막 user message 에 type 별 trigger 키워드.
      - 응답 content 가 이미 ':::artifact' 포함하지 않음 (idempotent).
      - HTML: content 가 ```html / <!DOCTYPE / <html 로 시작.
        mermaid: content 안에 ```mermaid ... ``` 블록 존재.
    React 는 export 패턴이 다양해 우선순위 밖.
    """
    try:
        msgs = req_body.get("messages") or []
        last_user = next((m for m in reversed(msgs)
                          if m.get("role") == "user"
                          and isinstance(m.get("content"), str)), None)
        if not last_user:
            return
        last_text = last_user["content"]
        choice = resp.get("choices", [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str):
            return

        # 모델이 자체 :::artifact 를 emit — well-formed(type="MIME" + ``` 펜스)면 그대로,
        # malformed(type:"html"/identifier 없음/펜스 없음 등)면 정규화. 모델이 Super Agent
        # 도구 base 프롬프트 영향으로 잘못된 래퍼를 내놓는 케이스를 결정적으로 보정.
        if ":::artifact" in content:
            if not _WELLFORMED_ART_RE.search(content):
                norm = _normalize_artifact(content)
                if norm is not None:
                    msg["content"] = norm
                    LOG.info("post-process: normalized malformed :::artifact")
            return

        # Artifact mode(토글 ON): 펜스 언어 기반 robust wrap 을 *먼저* 시도한다.
        # html/jsx/tsx/svg/mermaid 를 type 추론해 감싸고, 닫는 ``` 가 없는 truncated
        # 응답(큰 아티팩트가 max_tokens 에서 잘림)도 여는 펜스부터 끝까지 감싼다. 아래
        # 키워드(HTML/mermaid) 경로는 완전한 fence 만 처리하고 truncated 면 return 해
        # 버리므로, 더 견고한 이 경로를 우선한다. (artifact_mode 는 handler 가 *원본*
        # 메시지로 판정해 전달 — detox 후엔 system 에 :::artifact 가 없어 재판정 불가.)
        if artifact_mode:
            wrapped = _wrap_first_renderable_block(content)
            if wrapped is not None:
                msg["content"] = wrapped
                LOG.info("post-process: artifact wrap (fence-inferred)")
                return

        # HTML wrap path — trigger 매칭 시 content 어디서든 ```html fence 또는 raw
        # <!DOCTYPE.../html> 블록을 찾아 :::artifact 로 감싼다. 모델이 앞에 제목/설명을
        # 붙여도(예: "# To-Do 앱\n\n```html...") 그 텍스트는 보존하고 HTML 만 wrap.
        if _HTML_TRIGGER_PAT.search(last_text):
            m = re.search(r"```(?:html)?\s*\n(.*?)\n?```", content, re.DOTALL | re.IGNORECASE)
            if m:
                html, pre, post = m.group(1).rstrip(), content[:m.start()], content[m.end():]
            else:
                m2 = re.search(r"(?:<!DOCTYPE\b.*?</html\s*>|<html\b.*?</html\s*>)",
                               content, re.DOTALL | re.IGNORECASE)
                if not m2:
                    return
                html, pre, post = m2.group(0).strip(), content[:m2.start()], content[m2.end():]
            identifier = f"html-{uuid.uuid4().hex[:8]}"
            wrapped = (
                f":::artifact{{identifier=\"{identifier}\" type=\"text/html\" title=\"HTML\"}}\n"
                f"```\n{html}\n```\n"
                f":::"
            )
            msg["content"] = (pre.rstrip() + ("\n\n" if pre.strip() else "")) + wrapped + post
            LOG.info("post-process: html artifact wrap (identifier=%s, size=%d)", identifier, len(html))
            return

        # Mermaid wrap path — fence 추출 + 본문 wrap. 다른 텍스트 (설명) 가 같이 있으면
        # mermaid fence 만 wrap 하고 앞뒤 설명은 보존.
        if _MERMAID_TRIGGER_PAT.search(last_text):
            m = _MERMAID_FENCE_PAT.search(content)
            if m:
                mermaid = m.group(1).strip()
                identifier = f"diagram-{uuid.uuid4().hex[:8]}"
                wrapped = (
                    f":::artifact{{identifier=\"{identifier}\" type=\"application/vnd.mermaid\" title=\"Diagram\"}}\n"
                    f"```\n{mermaid}\n```\n"
                    f":::"
                )
                msg["content"] = content[:m.start()] + wrapped + content[m.end():]
                LOG.info("post-process: mermaid artifact wrap (identifier=%s, size=%d)", identifier, len(mermaid))
                return
    except Exception:
        LOG.exception("post_process_artifact failed (passing through)")


def _artifact_kw_intent(messages: list[dict[str, Any]] | None) -> bool:
    """user 의 마지막 message 에 HTML/mermaid trigger 키워드가 있는지.

    raw HTML/mermaid 를 모델이 :::artifact wrapper 없이 emit 할 가능성이 있는 케이스 —
    이때 non-stream + SSE wrap 으로 강제해 post-process(_post_process_artifact)로 감싼다.
    (별개로 artifacts 토글 ON 도 non-stream wrap 을 강제한다 — 모델이 :::artifact 를
    일관되게 안 emit 하고 ```lang 코드블록만 내놓기 때문. handler 의 _artifact_toggle_on 분기.)"""
    if not messages:
        return False
    last_user = next((m for m in reversed(messages)
                      if isinstance(m, dict)
                      and m.get("role") == "user"
                      and isinstance(m.get("content"), str)), None)
    if not last_user:
        return False
    t = last_user["content"]
    return bool(_HTML_TRIGGER_PAT.search(t) or _MERMAID_TRIGGER_PAT.search(t))


def _artifact_toggle_on(messages: list[dict[str, Any]] | None) -> bool:
    """artifacts 토글 ON 여부 — LibreChat 가 system 프롬프트에 내장 artifacts 지침을
    주입하면 ':::artifact' 가 system 메시지에 들어온다. 이게 켜져 있으면 첫 턴부터
    tool 을 막아 한 방에 아티팩트를 생성시킨다(반복 수정 시 degenerate 회피)."""
    if not messages:
        return False
    return any(isinstance(m, dict) and m.get("role") == "system"
               and isinstance(m.get("content"), str) and ":::artifact" in m["content"]
               for m in messages)


def _is_heavy(messages: list[dict[str, Any]] | None) -> bool:
    """Heuristic: does this request warrant the heavy reasoning model? True when
    the latest user message shows explicit deep-reasoning intent, OR total
    user/tool input is large (long pasted text / RAG context / many turns).
    System+assistant content excluded so the agent's big system prompt and the
    model's own prior output don't inflate the count."""
    if not messages:
        return False
    last_user = next((m for m in reversed(messages)
                      if isinstance(m, dict) and m.get("role") == "user"
                      and isinstance(m.get("content"), str)), None)
    if last_user and _HEAVY_KW_RE.search(last_user["content"]):
        return True
    user_chars = sum(
        len(m["content"]) for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "tool")
        and isinstance(m.get("content"), str)
    )
    return user_chars >= ROUTE_HEAVY_MIN_CHARS


def _select_chat_model(messages: list[dict[str, Any]] | None) -> str:
    """Pick the chat model for this request:
      1. ARTIFACT_MODEL override when set AND artifact mode is on (code/slide/web).
      2. ROUTE_HEAVY_MODEL when set, NOT artifact mode, and the heuristic judges
         the request heavy (deep reasoning / large input) → route to 122b.
      3. else CHAT_MODEL (gemma — also the default artifact model).
    Artifact mode stays on the gemma-tuned single-pass path (handled by 1)."""
    if ARTIFACT_MODEL and _artifact_toggle_on(messages):
        return ARTIFACT_MODEL
    if ROUTE_HEAVY_MODEL and not _artifact_toggle_on(messages) and _is_heavy(messages):
        return ROUTE_HEAVY_MODEL
    return CHAT_MODEL


def _artifact_intent(messages: list[dict[str, Any]] | None) -> bool:
    """artifact 응답 의도(broad) — 루프 차단기(tool_choice=none) 발동 조건.

    (1) artifacts 토글 ON: LibreChat 가 system 프롬프트에 내장 artifacts 지침을 주입
        → ':::artifact' 가 system 메시지에 들어온다. 토글이 진짜 신호이므로 user 키워드와
        무관하게 감지(예: "HTML 계산기 만들어줘" 처럼 키워드 패턴 밖이어도 루프 차단).
    (2) 폴백: HTML/mermaid trigger 키워드(_artifact_kw_intent).

    주의: 이건 루프 차단기 전용. non-stream 강제는 _artifact_kw_intent 만 사용해야 한다
    (broad 로 non-stream 을 강제하면 토글 켠 모든 응답이 2분 freeze)."""
    if not messages:
        return False
    for m in messages:
        if (isinstance(m, dict) and m.get("role") == "system"
                and isinstance(m.get("content"), str)
                and ":::artifact" in m["content"]):
            return True
    return _artifact_kw_intent(messages)


def _wrap_response_as_sse(resp: dict[str, Any]) -> AsyncIterator[bytes]:
    """Convert a non-streaming OpenAI chat completion into a minimal SSE stream
    (single chunk + [DONE]). Used so that a stream=True client request gets
    a streaming-shaped response even when we generated non-stream (artifact)."""
    chunk = {
        "id": resp.get("id", "chatcmpl-shim"),
        "object": "chat.completion.chunk",
        "created": resp.get("created", int(time.time())),
        "model": resp.get("model", "super-agent"),
        "choices": [
            {
                "index": 0,
                "delta": resp["choices"][0]["message"],
                "finish_reason": resp["choices"][0].get("finish_reason", "stop"),
            }
        ],
    }
    async def gen() -> AsyncIterator[bytes]:
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"
    return gen()


async def _artifact_completion(body: dict[str, Any], messages: list[dict[str, Any]],
                              chat_model: str) -> dict[str, Any]:
    """Single-pass artifact generation. gemma (CHAT_MODEL, multimodal + good code)
    serves artifacts directly: we replace LibreChat's verbose artifact prompt with
    the concise directive (_detox_artifact_messages), strip tools (one-shot, no
    tool loop), bump the token floor, and post-process the result into a
    well-formed :::artifact wrapper.

    No separate coder model and no draft+refine: the verbose artifact prompt is
    what made the model degenerate; the concise directive produces full, designed
    output in a single pass. `chat_model` is normally CHAT_MODEL but honours an
    ARTIFACT_MODEL override when one is configured."""
    abody = {**body, "messages": _detox_artifact_messages(messages),
             "max_tokens": max(int(body.get("max_tokens") or 0), ARTIFACT_MAX_TOKENS)}
    abody.pop("tools", None)
    abody.pop("tool_choice", None)
    return await _call_chat_nonstream(abody, chat_model, artifact_mode=True)


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    requested_stream = bool(body.get("stream"))
    tool_choice = body.get("tool_choice")
    messages = body.get("messages") or []
    last_role = messages[-1].get("role") if messages else None

    # Loop-breaker for artifact requests. The model otherwise loops on
    # web_search/execute_code (often degenerate empty-code → 400) and never
    # emits the final artifact.
    #
    # 두 모드:
    #  (A) artifacts 토글 ON (_artifact_toggle_on): system 에 :::artifact 지침 주입됨 =
    #      사용자가 '아티팩트로 답하라' 명시. 첫 턴부터 tool_choice="none" 로 강제해
    #      *한 방에* 생성시킨다. 1번이라도 tool 턴을 거치면(빌드·실행·수정) 모델이
    #      `a a a a a` 식 토큰 반복으로 degenerate 한다("수정하면 망가짐"). 한방이 제일 안전.
    #  (B) 키워드 기반(_artifact_intent, 토글 OFF): tool-result 턴(last_role=="tool")에서만
    #      강제 — 검색/페치 1회 후 생성하는 정상 orchestration 은 허용.
    if tool_choice != "none" and (
            _artifact_toggle_on(messages)
            or (last_role == "tool" and _artifact_intent(messages))):
        body["tool_choice"] = "none"
        tool_choice = "none"
        LOG.info("artifact → forcing tool_choice=none (toggle=%s, last_role=%s)",
                 _artifact_toggle_on(messages), last_role)

    # Snapshot of what LiteLLM currently exposes. None = probe never succeeded;
    # treat as "unknown" and let the best-effort flow surface errors as they happen.
    deployed = await _get_deployed_models()

    # gemma (CHAT_MODEL) is the single brain for everything, including artifacts
    # (single-pass with the concise directive — see _artifact_completion). An
    # ARTIFACT_MODEL override, if configured, only applies in artifact mode.
    chat_model = _select_chat_model(messages)
    artifact_mode = _artifact_toggle_on(messages)

    # Chat model not currently placed: nothing downstream can satisfy this
    # request. Short-circuit with a clear message so the client renders the
    # cause instead of a generic upstream error.
    if deployed is not None and chat_model not in deployed:
        msg = (f"[super-agent] chat model {chat_model!r} is not currently "
               f"deployed in LiteLLM (deployed: {sorted(deployed)}). "
               f"Wait for the scheduler to place it, or set CHAT_MODEL "
               f"to one of the deployed model_names.")
        LOG.warning(msg)
        if requested_stream:
            async def gen():
                for sse in _error_sse_chunks(msg):
                    yield sse
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse(_error_completion(msg))

    # Artifact mode forces non-stream so post-process can wrap the result into a
    # :::artifact block. (_artifact_kw_intent covers the toggle-off keyword case
    # where the model may emit raw HTML/mermaid we still want wrapped.)
    if requested_stream and (_artifact_kw_intent(messages) or artifact_mode):
        LOG.info("artifact mode — forcing non-stream for post-process wrap")
        resp = await (_artifact_completion(body, messages, chat_model) if artifact_mode
                      else _call_chat_nonstream(body, chat_model, artifact_mode))
        return StreamingResponse(_wrap_response_as_sse(resp),
                                 media_type="text/event-stream")
    if requested_stream:
        return StreamingResponse(_stream_chat(body, chat_model), media_type="text/event-stream")
    if artifact_mode:
        return JSONResponse(await _artifact_completion(body, messages, chat_model))
    return JSONResponse(await _call_chat_nonstream(body, chat_model, artifact_mode))
