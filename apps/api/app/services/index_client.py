"""Client for the retrieval index behind `/tools/index`.

Nothing here raises: writes report success as a boolean, a failed search returns
no passages. `search_knowledge` falls back to the lexical scorer over the same
documents, so an index that is down costs ranking quality, not answers.

The index is derived. Chunks and vectors are rebuildable from `StoredFile.text`,
which is what makes giving up quietly safe.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx

from app.services import settings_store

log = logging.getLogger(__name__)

#: Write embeds every chunk; read is one embedding and one query, with somebody
#: waiting on it.
_WRITE_TIMEOUT = httpx.Timeout(180.0, connect=8.0)
_READ_TIMEOUT = httpx.Timeout(25.0, connect=5.0)


def new_collection_key() -> str:
    """Unguessable name for one agent's shelf.

    The collection name is the whole authorisation at the index, so it is never
    derived from `agent_id`, which travels in URLs and API responses.
    """
    return secrets.token_urlsafe(24)


async def available() -> bool:
    return bool((await settings_store.tools_config()).index)


async def _base() -> str:
    return (await settings_store.tools_config()).index.rstrip("/")


async def put_document(
    *, collection: str, doc_id: str, name: str, text: str, source_url: str | None = None
) -> bool:
    """Index one document, replacing whatever was there under the same id."""
    base = await _base()
    if not base or not collection:
        return False
    try:
        async with httpx.AsyncClient(timeout=_WRITE_TIMEOUT) as client:
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
    log.debug("indexed %s: %s chunks", name, (body or {}).get("chunks"))
    return True


async def search(*, collection: str, query: str, limit: int = 4) -> list[dict[str, Any]]:
    """Nearest passages, or `[]` when the index cannot answer."""
    base = await _base()
    if not base or not collection:
        return []
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.post(
                f"{base}/search",
                json={"collection": collection, "query": query[:4000], "limit": limit},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("index search failed: %s", exc)
        return []
    return list((body or {}).get("passages") or [])


async def forget_document(*, collection: str, doc_id: str) -> bool:
    base = await _base()
    if not base or not collection:
        return False
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.delete(
                f"{base}/documents/{doc_id}", params={"collection": collection}
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("index delete for %s failed: %s", doc_id, exc)
        return False
    return True


async def forget_collection(*, collection: str) -> bool:
    """Drop a whole shelf. Triggered by agent deletion.

    Warned rather than logged at info: a collection outliving its agent stays
    searchable by whoever holds the key.
    """
    base = await _base()
    if not base or not collection:
        return False
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.delete(f"{base}/collections/{collection}")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("index collection %s not dropped: %s", collection, exc)
        return False
    return True
