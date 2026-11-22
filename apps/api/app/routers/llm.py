"""External OpenAI/Anthropic clients → LiteLLM proxy; client key forwarded, never master."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import settings
from app.services import governance, model_health, reasoning, settings_store, super_api
from app.services import models as model_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

#: hop-by-hop or httpx-regenerated headers
_DROP_REQUEST = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

#: model endpoints only; LiteLLM admin/key/spend/pass-through routes unreachable
_ALLOWED = re.compile(
    r"(?:v1/)?(?:chat/completions|completions|responses(?:/[A-Za-z0-9_-]{1,80}){0,2}"
    r"|messages(?:/count_tokens)?|embeddings|rerank"
    r"|models(?:/[A-Za-z0-9._:/-]{1,120})?)"
)
_ALLOWED_METHODS = {"GET", "POST", "DELETE"}

#: no audio to models (may be external); raw-body check covers every JSON shape
_AUDIO_PARTS = (b'"input_audio"', b'"audio_url"', b'"type":"audio"', b'"type": "audio"')
_MAX_BODY = 32 * 1024 * 1024

#: long coding-agent turns
_TIMEOUT = httpx.Timeout(settings.chat_timeout_sec, connect=10.0)


#: Routes whose body names a model and may ask for (no) reasoning.
_GENERATE = re.compile(r"(?:v1/)?(?:chat/completions|responses|messages)")
#: Chat completions: where the `kloudchat/super-agent` ids are answered by the app.
_CHAT = re.compile(r"(?:v1/)?chat/completions")
ROUTE_HEADER = "X-KloudChat-Route"
#: Model listing routes, answered with each model's reasoning levels added.
_LISTING = re.compile(r"(?:v1/)?models(?:/.+)?")


async def _reasoning_of(model_id: object) -> dict[str, Any] | None:
    """The catalogue's reasoning levels for `model_id`; None when unknown or unreadable."""
    if not isinstance(model_id, str):
        return None
    try:
        catalogue = await model_service.list_models()
    except Exception:  # noqa: BLE001 — the proxy forwards unchanged rather than fail
        return None
    found = model_service.find(catalogue.get("models") or [], model_id)
    return (found or {}).get("reasoning")


async def _settled(body: bytes) -> bytes:
    """`body` with an ask for no reasoning raised to the least, where the model cannot go
    without; unchanged otherwise (not JSON, unknown model, nothing to change)."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    if not reasoning.settle(payload, await _reasoning_of(payload.get("model"))):
        return body
    return json.dumps(payload, ensure_ascii=False).encode()


async def _with_reasoning(raw: bytes) -> bytes:
    """A LiteLLM model listing (or one model) with each model's `reasoning`:
    `{efforts, default, mandatory}`, or null where it takes no effort."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    items = payload.get("data") if isinstance(payload, dict) else None
    rows = items if isinstance(items, list) else [payload] if isinstance(payload, dict) else []
    try:
        catalogue = (await model_service.list_models()).get("models") or []
    except Exception:  # noqa: BLE001 — the listing still answers without the levels
        return raw
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            row["reasoning"] = (model_service.find(catalogue, row["id"]) or {}).get("reasoning")
    return json.dumps(payload, ensure_ascii=False).encode()


async def _with_super_agent(raw: bytes, clean: str, secret: str) -> bytes:
    """Model listing plus the Super Agent ids the key's owner may call."""
    if not clean.rstrip("/").endswith("models"):
        return raw
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return raw
    user = await super_api.caller_of(secret)
    if user is None:
        return raw
    payload["data"] = [*payload["data"], *await super_api.listed(user)]
    return json.dumps(payload, ensure_ascii=False).encode()


def _one_line(value: str) -> str:
    """Caller text flattened for logs."""
    return value.replace("\r", " ").replace("\n", " ")[:200]


def _openai_error(status_code: int, code: str, message: str) -> JSONResponse:
    kind = "invalid_request_error" if status_code < 500 else "api_error"
    return JSONResponse(
        {"error": {"message": message, "type": kind, "param": None, "code": code}},
        status_code=status_code,
    )


def _route_header(route: dict[str, Any]) -> str:
    return json.dumps(route, ensure_ascii=True, separators=(",", ":"))


def _stamp_sse(requested: str, route: dict[str, Any], on_usage: bool):
    """An SSE rewriter: each chunk names the requested model, and the route is added once,
    to the usage chunk the caller asked for (`on_usage`) or else the chunk that finishes the
    answer; a route-only chunk goes ahead of `[DONE]` if neither came."""
    state = {"buffer": b"", "sent": False}
    note = {"route": route}

    def event(raw: bytes) -> bytes:
        text = raw.decode("utf-8", "replace")
        if not text.startswith("data:"):
            return raw
        data = text[5:].strip()
        if data == "[DONE]":
            extra = b""
            if not state["sent"]:
                state["sent"] = True
                extra = (
                    b"data: "
                    + json.dumps(
                        {
                            "object": "chat.completion.chunk",
                            "model": requested,
                            "choices": [],
                            "kloudchat": note,
                        },
                        ensure_ascii=False,
                    ).encode()
                    + b"\n\n"
                )
            return extra + raw
        try:
            chunk = json.loads(data)
        except ValueError:
            return raw
        if not isinstance(chunk, dict):
            return raw
        chunk["model"] = requested
        finishing = any(
            isinstance(c, dict) and c.get("finish_reason") for c in chunk.get("choices") or []
        )
        if not state["sent"] and (chunk.get("usage") if on_usage else finishing):
            state["sent"] = True
            chunk["kloudchat"] = note
        return b"data: " + json.dumps(chunk, ensure_ascii=False).encode()

    def feed(data: bytes) -> bytes:
        state["buffer"] += data.replace(b"\r\n", b"\n")
        *done, state["buffer"] = state["buffer"].split(b"\n\n")
        return b"".join(event(e) + b"\n\n" for e in done)

    def finish() -> bytes:
        rest, state["buffer"] = state["buffer"], b""
        return event(rest) + b"\n\n" if rest.strip() else b""

    return feed, finish


async def _super_chat(
    base: str, headers: dict[str, str], body: bytes, secret: str
) -> Response | None:
    """The app's own answer to a chat request for a `kloudchat/super-agent` model; None where
    the request is no such call (or the key is none the gateway knows: it answers that)."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    requested = payload.get("model") if isinstance(payload, dict) else None
    if requested not in super_api.MODE_OF:
        return None
    user = await super_api.caller_of(secret)
    if user is None:
        return None
    if not await settings_store.api_super_agent_enabled():
        return _openai_error(404, "model_not_found", f"The model `{requested}` does not exist.")
    mode = super_api.MODE_OF[requested]
    stream = payload.get("stream") is True
    on_usage = bool((payload.get("stream_options") or {}).get("include_usage"))

    skip: frozenset[str] = frozenset()
    failed = False
    for attempt in (0, 1):
        try:
            resolved = await super_api.resolve(user, mode, payload, secret=secret, skip=skip)
        except super_api.ApiRouteError as exc:
            return _openai_error(exc.status, exc.code, exc.message)
        legacy = (await governance.current()).pii_masking
        sent = json.dumps(
            super_api.apply(payload, resolved, legacy=legacy), ensure_ascii=False
        ).encode()
        sent = await _settled(sent)
        route = {**resolved.route, "fallback": resolved.route["fallback"] or failed}
        chosen = str(resolved.model["id"])
        client = httpx.AsyncClient(timeout=_TIMEOUT)
        try:
            upstream = await client.send(
                client.build_request(
                    "POST", f"{base.rstrip('/')}/v1/chat/completions", headers=headers, content=sent
                ),
                stream=True,
            )
        except httpx.HTTPError:
            await client.aclose()
            model_health.mark_failed(chosen, "unreachable")
            if attempt == 0:
                skip, failed = skip | {chosen}, True
                continue
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="모델 서버에 연결하지 못했습니다."
            ) from None
        if upstream.status_code >= 500 and attempt == 0:
            error = f"upstream_{upstream.status_code}"
            await upstream.aclose()
            await client.aclose()
            model_health.mark_failed(chosen, error)
            skip, failed = skip | {chosen}, True
            continue
        break
    if upstream.status_code >= 500:
        model_health.mark_failed(chosen, f"upstream_{upstream.status_code}")
    elif upstream.status_code == 200:
        model_health.mark_ok(chosen)
    passthrough = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE}
    passthrough[ROUTE_HEADER] = _route_header(route)
    media = upstream.headers.get("content-type")
    ok = upstream.status_code == 200
    if ok and not stream and "json" in (media or ""):
        try:
            raw = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        try:
            answer = json.loads(raw)
        except ValueError:
            answer = None
        if isinstance(answer, dict):
            answer["model"] = requested
            answer["kloudchat"] = {"route": route}
            raw = json.dumps(answer, ensure_ascii=False).encode()
        return Response(content=raw, status_code=200, headers=passthrough, media_type=media)
    if ok and stream:
        feed, finish = _stamp_sse(requested, route, on_usage)

        async def relay_stamped():
            try:
                async for chunk in upstream.aiter_raw():
                    out = feed(chunk)
                    if out:
                        yield out
                tail = finish()
                if tail:
                    yield tail
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            relay_stamped(), status_code=200, headers=passthrough, media_type=media
        )

    async def relay_plain():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay_plain(), status_code=upstream.status_code, headers=passthrough, media_type=media
    )


@router.api_route(
    "/{path:path}", methods=["GET", "POST", "DELETE", "PATCH", "PUT"], include_in_schema=False
)
async def proxy(path: str, request: Request) -> Response:
    clean = path.strip("/")
    if (
        request.method not in _ALLOWED_METHODS
        or ".." in clean
        or "//" in clean
        or not _ALLOWED.fullmatch(clean)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    base, _ = await settings_store.litellm_config()
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="모델 서버가 설정되지 않았습니다.",
        )

    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST}
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="too_large"
        )
    if any(part in body for part in _AUDIO_PARTS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="음성은 내부 음성 인식으로만 처리합니다. 이 경로로는 보낼 수 없습니다.",
        )
    secret = super_api.bearer_of(request.headers.get("authorization", ""))
    if request.method == "POST" and _CHAT.fullmatch(clean):
        answered = await _super_chat(base, headers, body, secret)
        if answered is not None:
            return answered
    if request.method == "POST" and _GENERATE.fullmatch(clean):
        body = await _settled(body)

    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                url,
                headers=headers,
                content=body,
                params=dict(request.query_params),
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        log.info("llm proxy failed for %s: %s", _one_line(path), exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="모델 서버에 연결하지 못했습니다."
        ) from None

    async def relay():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE}
    if (
        request.method == "GET"
        and _LISTING.fullmatch(clean)
        and upstream.status_code == 200
        and "json" in upstream.headers.get("content-type", "")
    ):
        try:
            raw = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()
        return Response(
            content=await _with_super_agent(await _with_reasoning(raw), clean, secret),
            status_code=200,
            headers=passthrough,
            media_type=upstream.headers.get("content-type"),
        )
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )
