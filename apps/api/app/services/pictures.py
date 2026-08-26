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

#: The most a picture may weigh before a turn leaves it out.
#:
#: Base64 adds a third again on the way into a prompt, and a context window
#: spent on one screenshot is a conversation that stops answering. Four
#: megabytes is a full-page scan at a readable resolution.
MAX_PICTURE_BYTES = 4_000_000


def can_be_seen(mime: str, size: int) -> bool:
    """Whether a model could be handed this file as a picture.

    The same list a document is allowed to carry, for the same reason: raster
    only, because `image/svg+xml` is a document that can carry script.
    """
    return (mime or "").lower() in EMBEDDABLE and 0 < size <= MAX_PICTURE_BYTES


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


__all__ = [
    "EMBEDDABLE",
    "MAX_PICTURE_BYTES",
    "can_be_seen",
    "data_uri",
    "decode",
    "encode",
    "is_embedded",
]
