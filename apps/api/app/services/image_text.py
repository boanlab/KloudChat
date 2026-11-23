"""Exact text on a picture drawn by an in-house model, set in a real font.

An in-house picture model draws Hangul with wrong letters, and the planner cannot fix that. So the
model is asked for a picture with no text at all, and the words the person put in quotes are
set on it here: the letters are always the ones they wrote.
"""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/kloudchat"),
    Path("/usr/share/fonts/truetype/nanum"),
    Path("/usr/share/fonts/nanum"),
)
#: look of the type → font file. All are SIL Open Font License 1.1 (Nanum from Debian's
#: fonts-nanum, the rest fetched at image build; see apps/api/Dockerfile and NOTICE).
FONTS = {
    "gothic": "NanumSquareRoundB.ttf",
    "serif": "NanumMyeongjoBold.ttf",
    "heavy": "BlackHanSans-Regular.ttf",
    "clean": "DoHyeon-Regular.ttf",
    "round": "Jua-Regular.ttf",
    "hand": "Gaegu-Bold.ttf",
    "brush": "NanumBrushScript-Regular.ttf",
    "cute": "YeonSung-Regular.ttf",
}
_FALLBACKS = ("NanumSquareRoundB.ttf", "NanumGothicBold.ttf", "NanumGothic.ttf")

#: where a line sits, as a fraction of the picture's height (its centre)
WHERE = {"top": 0.14, "upper": 0.32, "center": 0.5, "lower": 0.68, "bottom": 0.86}
#: font size as a fraction of the picture's width, before it is shrunk to fit
SIZE = {"large": 0.095, "medium": 0.055, "small": 0.034}
#: widest a line may be, as a fraction of the picture's width
_MAX_WIDTH = 0.84
_MAX_STRINGS = 6
_MAX_LENGTH = 60

_QUOTED = re.compile(rf"[「『“\"]([^」』”\"\n]{{1,{_MAX_LENGTH}}})[」』”\"]")


def quoted_strings(request: str) -> list[str]:
    """The words the person put in quotes, in order and without repeats."""
    found: list[str] = []
    for match in _QUOTED.finditer(request):
        text = " ".join(match.group(1).split())
        if text and text not in found:
            found.append(text)
    return found[:_MAX_STRINGS]


def without_quoted(request: str) -> str:
    """The request with the quoted words taken out, for the picture model that draws no text."""
    return " ".join(_QUOTED.sub(" ", request).split())


@dataclass(slots=True)
class Line:
    text: str
    where: str = "center"
    size: str = "medium"
    #: a key of `FONTS`
    font: str = "gothic"


def default_layout(strings: list[str]) -> list[Line]:
    """First string a large title in the upper half, the rest small captions along the bottom."""
    lines = []
    for index, text in enumerate(strings):
        if index == 0:
            lines.append(Line(text, "upper" if len(strings) > 1 else "center", "large"))
        else:
            lines.append(Line(text, "bottom" if index % 2 else "lower", "small"))
    return lines


def parse_layout(raw: Any, strings: list[str], font: str = "gothic") -> list[Line]:
    """The planner's placement, one entry per quoted string; anything unusable falls back to the
    default for that string. The text itself always comes from `strings`."""
    fallback = [Line(base.text, base.where, base.size, font) for base in default_layout(strings)]
    try:
        rows = raw if isinstance(raw, list) else json.loads(raw)["layout"]
    except (TypeError, ValueError, KeyError):
        return fallback
    out = []
    for index, base in enumerate(fallback):
        row = rows[index] if isinstance(rows, list) and index < len(rows) else {}
        row = row if isinstance(row, dict) else {}
        where = row.get("where") if row.get("where") in WHERE else base.where
        size = row.get("size") if row.get("size") in SIZE else base.size
        out.append(Line(base.text, where, size, font))
    return out


def _path(name: str) -> Path | None:
    for folder in _FONT_DIRS:
        if (folder / name).exists():
            return folder / name
    return None


def _font(size: int, role: str = "gothic") -> ImageFont.FreeTypeFont:
    """The role's font, else the first plain Korean face that is installed."""
    for name in (FONTS.get(role, ""), *_FALLBACKS):
        if name and (path := _path(name)):
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _covers(font: ImageFont.FreeTypeFont, text: str) -> bool:
    """Whether the font has a glyph for every visible character: a missing one is drawn as the
    font's empty-box glyph, whose size and width a real letter does not share."""
    box = (font.getbbox("\uffff"), font.getlength("\uffff"))
    return not any(
        (font.getbbox(char), font.getlength(char)) == box
        for char in text
        if not char.isspace()
    )


#: a letter some Korean display faces lack → look-alikes to try in the same face
_LOOKALIKES = {
    "·": "ㆍ•∙⋅・-", "~": "∼〜", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
}


def _font_for(text: str, size: int, role: str) -> tuple[ImageFont.FreeTypeFont, str]:
    """(font, text): the role's font with any missing letter swapped for a look-alike it has, or
    the plain Korean face when the role's font still cannot draw the whole text."""
    font = _font(size, role)
    if _covers(font, text):
        return font, text
    swapped = []
    for char in text:
        if _covers(font, char):
            swapped.append(char)
            continue
        swapped.append(next((c for c in _LOOKALIKES.get(char, "") if _covers(font, c)), char))
    shown = "".join(swapped)
    if _covers(font, shown):
        return font, shown
    return _font(size, "gothic"), text


def _split(text: str, draw: ImageDraw.ImageDraw, font, limit: float) -> list[str]:
    """One line if it fits, else two balanced at a space (or at the middle of an unspaced word)."""
    if draw.textlength(text, font=font) <= limit:
        return [text]
    words = text.split(" ")
    if len(words) > 1:
        best = min(
            range(1, len(words)),
            key=lambda cut: abs(
                draw.textlength(" ".join(words[:cut]), font=font)
                - draw.textlength(" ".join(words[cut:]), font=font)
            ),
        )
        return [" ".join(words[:best]), " ".join(words[best:])]
    half = len(text) // 2
    return [text[:half], text[half:]]


def _fit(text: str, draw: ImageDraw.ImageDraw, width: int, start: int, role: str):
    """(font, lines): the largest size, down to 55% of `start`, at which no line is too wide."""
    limit = width * _MAX_WIDTH
    size = start
    while True:
        font, shown = _font_for(text, size, role)
        lines = _split(shown, draw, font, limit)
        if all(draw.textlength(line, font=font) <= limit for line in lines) or size <= start * 0.55:
            return font, lines
        size = max(int(size * 0.92), 8)


def _luminance(region: Image.Image) -> float:
    grey = region.convert("L").resize((1, 1), Image.Resampling.BOX)
    return grey.getpixel((0, 0)) / 255


def overlay(data: bytes, lines: list[Line]) -> bytes:
    """The picture (PNG bytes) with each line set in a font, on a soft plate so it reads on any
    background. Returns the input unchanged when it cannot be read."""
    if not lines:
        return data
    try:
        picture = Image.open(io.BytesIO(data)).convert("RGBA")
    except OSError:
        return data
    width, height = picture.size
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    taken: list[tuple[int, int, int, int]] = []
    for line in lines:
        font, parts = _fit(
            line.text, draw, width, max(int(width * SIZE[line.size]), 10), line.font
        )
        ascent, descent = font.getmetrics()
        row = ascent + descent
        gap = int(row * 0.18)
        block_w = int(max(draw.textlength(part, font=font) for part in parts))
        block_h = row * len(parts) + gap * (len(parts) - 1)
        pad_x, pad_y = int(row * 0.55), int(row * 0.32)
        left = (width - block_w) // 2
        top = int(height * WHERE[line.where] - block_h / 2)
        top = max(pad_y, min(top, height - block_h - pad_y))
        box = (left - pad_x, top - pad_y, left + block_w + pad_x, top + block_h + pad_y)
        # never over an earlier line: move down, or up when at the bottom edge
        step = row + gap
        for _ in range(8):
            if not any(box[0] < t[2] and box[2] > t[0] and box[1] < t[3] and box[3] > t[1]
                       for t in taken):
                break
            down = box[3] + step <= height
            shift = step if down else -step
            box = (box[0], box[1] + shift, box[2], box[3] + shift)
            top += shift
        taken.append(box)
        dark = _luminance(picture.crop(tuple(max(0, v) for v in box))) < 0.5
        plate = (0, 0, 0, 170) if dark else (255, 255, 255, 215)
        ink = (255, 255, 255, 255) if dark else (30, 30, 36, 255)
        draw.rounded_rectangle(box, radius=int(row * 0.45), fill=plate)
        for index, part in enumerate(parts):
            span = draw.textlength(part, font=font)
            draw.text(
                ((width - span) / 2, top + index * (row + gap)), part, font=font, fill=ink
            )
    out = io.BytesIO()
    Image.alpha_composite(picture, layer).convert("RGB").save(out, format="PNG")
    return out.getvalue()


_LAYOUT_PROMPT = """너는 그림 모델에 줄 프롬프트와 글자 배치를 정하는 기획자다.
아래 요청의 따옴표 안 글자는 나중에 글꼴로 그림 위에 얹는다. 그림 모델은 글자를 못 그린다.

할 일:
1. "prompt": 그림 모델에 줄 **영어 프롬프트 하나**(80~160 단어). 무엇을 어떤 분위기로 그리는지,
   구도, 색, 조명을 적는다. 글자·숫자·간판·말풍선·워터마크는 그리지 않는다고 분명히 적는다.
   글자를 얹을 자리는 비워 둔다(예: calm empty area in the upper half). 따옴표 안 글자는
   프롬프트에 쓰지 마라. 제목·행사명·기관명을 영어로 옮겨 적는 것도 안 된다(그림 모델이 그 말을
   그림 안에 글자로 그려 버린다). 장면과 분위기만 쓴다.
2. "font": 글자 모양 하나. gothic(바른 고딕·안내문), serif(명조·격식·학술·전통), heavy(아주 굵은
   제목·행사·세일), clean(깔끔한 현대식·IT), round(둥글고 친근함·어린이), hand(손글씨·일상·감성),
   brush(붓글씨·한국적·축제), cute(귀엽고 캐주얼한 이벤트) 중 하나. 그림의 분위기와 쓰임에 맞춘다.
3. "layout": 따옴표 글자마다 하나, 같은 순서. "where"는 top, upper, center, lower, bottom 중 하나,
   "size"는 large, medium, small 중 하나. 제목은 large, 부제나 날짜는 medium, 작은 안내는 small.
   같은 자리에 둘을 겹치지 마라.

글자(순서대로):
{strings}

요청:
{request}

JSON 하나만 출력한다. 설명, 머리말, 코드펜스는 붙이지 마라.
{{"prompt": "...", "font": "gothic", "layout": [{{"where": "upper", "size": "large"}}]}}"""


def layout_request(request: str, strings: list[str]) -> str:
    listed = "\n".join(f"{n}. {text}" for n, text in enumerate(strings, start=1))
    return _LAYOUT_PROMPT.format(strings=listed, request=request.strip()[:2000])


def parse_plan(raw: str, strings: list[str], request: str) -> tuple[str, list[Line]]:
    """(picture prompt, lines) from the planner's JSON; the picture prompt falls back to the
    request without its quoted words, the lines to the default layout."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*|\s*```$", "", text).strip()
    try:
        body = json.loads(text)
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    prompt = str(body.get("prompt") or "").strip()
    # a quoted word the planner copied into the picture prompt would be drawn as wrong letters
    if not prompt or any(word in prompt for word in strings):
        prompt = without_quoted(request)
    font = str(body.get("font") or "")
    return prompt, parse_layout(body.get("layout"), strings, font if font in FONTS else "gothic")
