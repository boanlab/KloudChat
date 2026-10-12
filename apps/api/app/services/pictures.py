"""Document pictures: embedded raster `data:` URIs only (no remote fetch, no SVG script)."""

from __future__ import annotations

import base64
import binascii
import re

#: embeddable MIME types (also the embedding endpoint's)
EMBEDDABLE = ("image/png", "image/jpeg", "image/gif", "image/webp")

#: max picture size handed to a model
MAX_PICTURE_BYTES = 4_000_000


def can_be_seen(mime: str, size: int) -> bool:
    """Whether this file can go to a model as a picture."""
    return (mime or "").lower() in EMBEDDABLE and 0 < size <= MAX_PICTURE_BYTES


_DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", re.I)


def is_embedded(src: str) -> bool:
    """Whether `src` is an embedded picture."""
    return bool(_DATA_URI.match((src or "").strip()))


def data_uri(mime: str, encoded: str) -> str:
    return f"data:{mime};base64,{encoded}"


def encode(mime: str, data: bytes) -> str:
    return data_uri(mime, base64.b64encode(data).decode("ascii"))


def decode(src: str) -> tuple[str, bytes] | None:
    """`(mime, bytes)` for an embedded picture, else `None` (never fetched)."""
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
