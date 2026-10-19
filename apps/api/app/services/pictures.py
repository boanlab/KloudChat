"""Document pictures: embedded raster `data:` URIs only (no remote fetch, no SVG script)."""

from __future__ import annotations

import base64
import binascii
import io
import re

from PIL import Image

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


#: `![alt](data:image/…)` to its `)` or, unclosed, to the end of the line
_EMBED = re.compile(r"!\[([^\]\n]*)\]\((data:image/[^)\n]*)\)?[ \t]*")
#: 「(그림 1)」, 「(그림 1 참조)」 pointing at a picture
_MENTION = r"\s?[(（]\s*그림\s*{n}(?:\s*참조)?\s*[)）]"


def opens(src: str) -> bool:
    """Whether `src` is an embedded picture that decodes to a real image."""
    decoded = decode(src)
    if decoded is None:
        return False
    try:
        with Image.open(io.BytesIO(decoded[1])) as picture:
            picture.verify()
    except Exception:  # noqa: BLE001 — any decode failure means not a picture
        return False
    return True


def drop_broken(markdown: str) -> str:
    """Markdown without embedded pictures that do not open, nor 「(그림 N)」 pointing at one."""
    gone: list[str] = []

    def keep(match: re.Match) -> str:
        if match.group(0).rstrip().endswith(")") and opens(match.group(2)):
            return match.group(0)
        if number := re.search(r"그림\s*(\d+)", match.group(1)):
            gone.append(number.group(1))
        return ""

    text = _EMBED.sub(keep, markdown or "")
    if text == markdown:
        return text
    for n in gone:
        text = re.sub(_MENTION.format(n=n), "", text)
    return re.sub(r"\n{3,}", "\n\n", text)


__all__ = [
    "EMBEDDABLE",
    "MAX_PICTURE_BYTES",
    "can_be_seen",
    "data_uri",
    "decode",
    "drop_broken",
    "encode",
    "is_embedded",
    "opens",
]
