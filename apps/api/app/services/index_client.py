"""Client for the retrieval index behind `/tools/index`.

Nothing here raises: writes report success as a boolean, a failed search returns
no passages. `search_knowledge` falls back to the lexical scorer over the same
documents, so an index that is down costs ranking quality, not answers.

The index is derived. Chunks and vectors are rebuildable from `StoredFile.text`,
which is what makes giving up quietly safe.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any

import httpx

from app.core import logs
from app.services import settings_store

#: A stored file's id: 32 hex characters, as `uuid4().hex` writes it.
_ID = re.compile(r"[0-9a-f]{32}")

#: A shelf's name, as `new_collection_key` writes it: urlsafe base64 of 24
#: bytes. Checked for the same reason the id is — it lands in a URL path.
_KEY = re.compile(r"[A-Za-z0-9_-]{32}")

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
        log.info("index search failed: %s", logs.safe(exc))
        return []
    return list((body or {}).get("passages") or [])


async def forget_document(*, collection: str, doc_id: str) -> bool:
    # The id goes into the path, so it has to be an id. Every caller passes a
    # `files.id`, which is 32 hex characters — but the check is here rather
    # than assumed, because a value that reaches a URL decides which host and
    # which path the request lands on, and `../` is a legal filename.
    if not _ID.fullmatch(doc_id):
        log.warning("index delete refused a malformed id: %s", logs.safe(doc_id))
        return False
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
        log.warning("index delete for %s failed: %s", doc_id, logs.safe(exc))
        return False
    return True


async def forget_collection(*, collection: str) -> bool:
    """Drop a whole shelf. Triggered by agent deletion.

    Warned rather than logged at info: a collection outliving its agent stays
    searchable by whoever holds the key.
    """
    if not _KEY.fullmatch(collection):
        log.warning("index drop refused a malformed key: %s", logs.safe(collection))
        return False
    base = await _base()
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.delete(f"{base}/collections/{collection}")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("index collection not dropped: %s", logs.safe(exc))
        return False
    return True
