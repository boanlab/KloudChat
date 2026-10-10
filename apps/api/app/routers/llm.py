"""Reverse proxy from external OpenAI/Anthropic-compatible clients to LiteLLM.

    OPENAI_BASE_URL=https://<this-server>/llm/v1
    ANTHROPIC_BASE_URL=https://<this-server>/llm

Authentication is by key, not session: the Authorization header is forwarded
unchanged and LiteLLM validates it. The master key is never attached.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.services import settings_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])

#: Headers not forwarded: hop-by-hop, or ones httpx has to regenerate itself.
_DROP_REQUEST = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

#: The model endpoints external tools use, and nothing else: LiteLLM's admin UI, key/user
#: management, spend and pass-through routes are never reachable from outside.
_ALLOWED = re.compile(
    r"(?:v1/)?(?:chat/completions|completions|responses(?:/[A-Za-z0-9_-]{1,80}){0,2}"
    r"|messages(?:/count_tokens)?|embeddings|rerank"
    r"|models(?:/[A-Za-z0-9._:/-]{1,120})?)"
)
_ALLOWED_METHODS = {"GET", "POST", "DELETE"}

#: Audio is transcribed only by the internal Whisper; no audio part goes to a model
#: through this door (a model behind LiteLLM may be external). Checked on the raw body
#: so no JSON shape (chat, responses, messages) slips past.
_AUDIO_PARTS = (b'"input_audio"', b'"audio_url"', b'"type":"audio"', b'"type": "audio"')
_MAX_BODY = 32 * 1024 * 1024

#: Same as the chat timeout: a coding agent's turn is long.
_TIMEOUT = httpx.Timeout(settings.chat_timeout_sec, connect=10.0)


def _one_line(value: str) -> str:
    """Caller-controlled text, flattened before it reaches a log line."""
    return value.replace("\r", " ").replace("\n", " ")[:200]


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
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )
