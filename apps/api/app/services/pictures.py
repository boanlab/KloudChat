"""Pictures inside documents: embedded `data:` URIs only, raster only.

Documents are downloaded and shared by link, so a remote address would be a
request made on the reader's behalf. SVG is excluded because it can carry script.
"""

from __future__ import annotations

import base64
import binascii
import re

#: MIME types a document may carry; also what the embedding endpoint accepts.
EMBEDDABLE = ("image/png", "image/jpeg", "image/gif", "image/webp")

#: Largest picture a turn will hand to a model.
MAX_PICTURE_BYTES = 4_000_000


def can_be_seen(mime: str, size: int) -> bool:
    """Whether a model could be handed this file as a picture."""
    return (mime or "").lower() in EMBEDDABLE and 0 < size <= MAX_PICTURE_BYTES


_DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", re.I)


def is_embedded(src: str) -> bool:
    """Whether this address is a picture already inside the file."""
    return bool(_DATA_URI.match((src or "").strip()))


def data_uri(mime: str, encoded: str) -> str:
    """The `<img src>` form."""
    return f"data:{mime};base64,{encoded}"


def encode(mime: str, data: bytes) -> str:
    return data_uri(mime, base64.b64encode(data).decode("ascii"))


def decode(src: str) -> tuple[str, bytes] | None:
    """`(mime, bytes)` for an embedded picture, or `None` for anything else (never fetched)."""
    match = _DATA_URI.match((src or "").strip())
    if not match:
        return None
    try:
        return (
            match.group(1).lower().replace("jpg", "jpeg"),
            base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True),
        )
    except (binascii.Error, ValueError):
        return None


__all__ = [
    "EMBEDDABLE",
    "MAX_PICTURE_BYTES",
    "can_be_seen",
    "data_uri",
    "decode",
    "encode",
    "is_embedded",
]
