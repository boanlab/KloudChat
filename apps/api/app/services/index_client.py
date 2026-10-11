"""Retrieval index client (`/tools/index`); never raises."""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

import httpx

from app.core import logs
from app.services import settings_store, tools_gateway

#: stored file id (`uuid4().hex`)
_ID = re.compile(r"[0-9a-f]{32}")

#: collection key; lands in a URL path
_KEY = re.compile(r"[A-Za-z0-9_-]{32}")

log = logging.getLogger(__name__)

#: write embeds every chunk; read = one embedding + one query
_WRITE_TIMEOUT = httpx.Timeout(180.0, connect=8.0)
_READ_TIMEOUT = httpx.Timeout(25.0, connect=5.0)


def new_collection_key() -> str:
    """Unguessable collection name; never from public `agent_id`."""
    return secrets.token_urlsafe(24)


#: usage-row embedder name; shim doesn't report it
EMBED_MODEL = "local/bge-m3"

#: chunks embedded by the last `put_document`, for usage recording
last_chunks = 0


async def available() -> bool:
    return bool((await settings_store.tools_config()).index)


async def _base() -> str:
    return (await settings_store.tools_config()).index.rstrip("/")


async def put_document(
    *, collection: str, doc_id: str, name: str, text: str, source_url: str | None = None
) -> bool:
    """Index one document, replacing the same id."""
    base = await _base()
    if not base or not collection:
        return False
    try:
        async with tools_gateway.client(timeout=_WRITE_TIMEOUT) as client:
            response = await client.put(
                f"{base}/documents",
                json={
                    "collection": collection,
                    "doc_id": doc_id,
                    "name": name,
                    "text": text,
                    "source_url": source_url,
                },
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("index write for %s failed: %s", name, exc)
        return False
    chunks = (body or {}).get("chunks")
    log.debug("indexed %s: %s chunks", name, chunks)
    global last_chunks
    last_chunks = int(chunks) if isinstance(chunks, int | float) else 0
    return True


async def search(*, collection: str, query: str, limit: int = 4) -> list[dict[str, Any]]:
    """Nearest passages, or `[]` on failure."""
    base = await _base()
    if not base or not collection:
        return []
    try:
        async with tools_gateway.client(timeout=_READ_TIMEOUT) as client:
            response = await client.post(
                f"{base}/search",
                json={"collection": collection, "query": query[:4000], "limit": limit},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("index search failed: %s", logs.safe(exc))
        return []
    return list((body or {}).get("passages") or [])


async def forget_document(*, collection: str, doc_id: str) -> bool:
    # id goes into the URL path
    if not _ID.fullmatch(doc_id):
        log.warning("index delete refused a malformed id: %s", logs.safe(doc_id))
        return False
    base = await _base()
    if not base or not collection:
        return False
    try:
        async with tools_gateway.client(timeout=_READ_TIMEOUT) as client:
            response = await client.delete(
                f"{base}/documents/{doc_id}", params={"collection": collection}
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("index delete for %s failed: %s", logs.safe(doc_id), logs.safe(exc))
        return False
    return True


async def forget_collection(*, collection: str) -> bool:
    """Drop a whole collection."""
    if not _KEY.fullmatch(collection):
        log.warning("index drop refused a malformed key: %s", logs.safe(collection))
        return False
    base = await _base()
    if not base:
        return False
    try:
        async with tools_gateway.client(timeout=_READ_TIMEOUT) as client:
            response = await client.delete(f"{base}/collections/{collection}")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("index collection not dropped: %s", logs.safe(exc))
        return False
    return True
