"""Label widths in points with the figure face, and labels fitted to their boxes."""

from __future__ import annotations

import re
from functools import lru_cache
from itertools import combinations
from pathlib import Path

#: Pretendard (the figure face) first; Nanum Gothic is close enough to measure with
_FACES = {
    False: (
        "/usr/share/fonts/opentype/pretendard/Pretendard-Regular.otf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ),
    True: (
        "/usr/share/fonts/opentype/pretendard/Pretendard-Bold.otf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ),
}
#: line height, in font sizes
LINE = 1.25
#: a label may shrink this far to stay on one line
SHRINK_ONE_LINE = 0.85


@lru_cache(maxsize=4)
def _face(bold: bool):
    """Face measured at 100 pt and scaled (FreeType hints small sizes to whole pixels)."""
    from PIL import ImageFont

    for path in _FACES[bold]:
        if Path(path).is_file():
            return ImageFont.truetype(path, 100)
    return None


def _ems(text: str) -> float:
    """Width in ems without a face: Hangul ~0.92, space 0.26, other 0.56."""
    return sum(0.92 if ord(ch) >= 0x1100 else 0.26 if ch == " " else 0.56 for ch in text)


def width(text: str, size: float, bold: bool = False) -> float:
    """`text` across in points at `size`."""
    face = _face(bold)
    if face is None:
        return _ems(text) * size
    return face.getlength(text) * size / 100


#: joins a word to the hyphen-ended piece before it (no space between)
_GLUE = "\0"


def _hyphen_words(words: list[str]) -> list[str]:
    """`words` also broken after their hyphens (「kube-」「controller-」「manager」)."""
    out: list[str] = []
    for word in words:
        pieces = re.findall(r"[^-]+-+|[^-]+$|-+", word)
        out += [p + _GLUE for p in pieces[:-1]] + pieces[-1:]
    return out


def _balanced(words: list[str], n: int, size: float, bold: bool) -> list[str]:
    """`words` in `n` lines, broken at the spaces that make the widest line narrowest."""
    best: list[str] = words
    best_w = float("inf")
    for cuts in combinations(range(1, len(words)), n - 1):
        bounds = (0, *cuts, len(words))
        lines = [" ".join(words[a:b]).replace(_GLUE + " ", "").rstrip(_GLUE)
                 for a, b in zip(bounds, bounds[1:], strict=False)]
        w = max(width(line, size, bold) for line in lines)
        if w < best_w:
            best, best_w = lines, w
    return best


def _char_wrap(text: str, size: float, bold: bool, limit: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for ch in text:
        if line and width(line + ch, size, bold) > limit:
            lines.append(line.rstrip())
            line = ch.lstrip()
        else:
            line += ch
    return [*lines, line]


def fit(
    text: str, w: float, h: float, size: float, bold: bool = False,
    min_size: float = 9.0, pad: float = 8.0,
) -> tuple[list[str], float]:
    """Lines and size for `text` inside `w` × `h` points.

    One line first, shrinking up to SHRINK_ONE_LINE; then lines broken at spaces, balanced,
    shrinking to `min_size`; then at hyphens too; last, breaks between characters at `min_size`.
    """
    room_w, room_h = w - 2 * pad, h - pad
    sizes = [size - 0.5 * k for k in range(int((size - min_size) / 0.5) + 1)]
    if "\n" in text:  # the writer's own breaks are kept
        paras = text.split("\n")
        for s in sizes:
            if len(paras) * s * LINE <= room_h and max(width(p, s, bold) for p in paras) <= room_w:
                return paras, s
        return paras, min_size
    for s in sizes:
        if s < size * SHRINK_ONE_LINE:
            break
        if width(text, s, bold) <= room_w and s * LINE <= room_h:
            return [text], s
    spaced = text.split(" ")
    for words in (spaced, _hyphen_words(spaced)):
        for s in sizes:
            for n in range(2, min(len(words), 4) + 1):
                if n * s * LINE > room_h:
                    break
                lines = _balanced(words, n, s, bold)
                if max(width(line, s, bold) for line in lines) <= room_w:
                    return lines, s
        if "-" not in text:
            break
    return _char_wrap(text, min_size, bold, room_w), min_size


__all__ = ["LINE", "fit", "width"]
