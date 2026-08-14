"""The path external tools take to this instance's models.

LiteLLM lives on the private network and this API is the only thing a browser
can reach, so an OpenAI- or Anthropic-compatible client — a coding agent, say —
has to come through here.

    OPENAI_BASE_URL=https://<this-server>/llm/v1
    ANTHROPIC_BASE_URL=https://<this-server>/llm

**Authentication is by key, not by session.** The incoming Authorization header
is forwarded upstream unchanged and LiteLLM decides whether it is valid, because
that key *is* a LiteLLM virtual key. Spend, budget and the model allow-list all
follow it, so this path cannot grant more than the user already has.

The master key never travels this way. With no credential to forward, upstream
answers 401.
"""

from __future__ import annotations

import logging

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

#: A coding agent's turn is long. Same value as the chat timeout.
_TIMEOUT = httpx.Timeout(settings.chat_timeout_sec, connect=10.0)


@router.api_route(
    "/{path:path}", methods=["GET", "POST", "DELETE", "PATCH", "PUT"], include_in_schema=False
)
async def proxy(path: str, request: Request) -> Response:
    base, _ = await settings_store.litellm_config()
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="모델 서버가 설정되지 않았습니다.",
        )

    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST}
    body = await request.body()

    client = httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request(
                request.method, url, headers=headers, content=body,
                params=dict(request.query_params),
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        log.info("llm proxy failed for %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="모델 서버에 연결하지 못했습니다."
        ) from None

    async def relay():
        try:
            # Relayed chunk by chunk. Collecting first would make streaming
            # not streaming.
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE
    }
    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )
