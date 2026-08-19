"""A picture inside a document: the one form of it this product allows.

Artifacts render in a `sandbox=""` iframe, are downloaded, opened outside it
and shared by link. An address in one is a request made on the reader's behalf
from a document they did not write — so the only picture a document may carry
is one already inside it, as a `data:` URI.

Raster only. `image/svg+xml` is a document that can carry script, which is the
thing that rule exists to keep out.

The same fact was being stated in four places — the sanitiser, the HTML
reader, the deck renderer and the router that embeds one — so it is stated
here instead.
"""

from __future__ import annotations

import base64
import binascii
import re

#: What a picture may be. Also what the embedding endpoint accepts, so a file
#: this cannot draw is refused where it is chosen rather than where it fails.
EMBEDDABLE = ("image/png", "image/jpeg", "image/gif", "image/webp")

_DATA_URI = re.compile(r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,([A-Za-z0-9+/=\s]+)$", re.I)


def is_embedded(src: str) -> bool:
    """Whether this address is a picture already inside the file."""
    return bool(_DATA_URI.match((src or "").strip()))


def data_uri(mime: str, encoded: str) -> str:
    """The address form: what goes in an `<img src>`."""
    return f"data:{mime};base64,{encoded}"


def encode(mime: str, data: bytes) -> str:
    return data_uri(mime, base64.b64encode(data).decode("ascii"))


def decode(src: str) -> tuple[str, bytes] | None:
    """`(mime, bytes)` for an embedded picture, or `None` for anything else.

    Anything else includes a remote address, which cannot be stored and must
    not be fetched if it somehow is.
    """
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


__all__ = ["EMBEDDABLE", "data_uri", "decode", "encode", "is_embedded"]
