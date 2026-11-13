"""The time-coded document of a video: what was said and what was shown, by time.

Pure functions (no I/O), used by `video_jobs`:

- `audio_pieces`: the audio cut into Whisper-sized pieces that overlap a little;
- `merge_said`: the pieces' segments on one timeline, the overlap kept once;
- `sections`: scenes (from frame times) holding the speech that falls in them, long talks
  split so every passage still sits close to a timestamp;
- `render`: the file's text, one section per timestamp:

      [00:12:30]
      (화면) 슬라이드 — 중독의 뇌 회로 | 글자: Reward · Motivation · Memory
      (말) 보상 회로가 바뀌면 …

Timestamps are always `HH:MM:SS` (hours too, zero-padded): `knowledge` and the web read them
back to cite and jump to the moment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: one Whisper request's audio, seconds (16 kHz mono FLAC: ~10 MB, under every upload limit);
#: under vLLM Whisper's 600 s ceiling even when a short tail is folded into the last piece
PIECE_SEC = 580.0
#: audio both neighbouring pieces hear, so a word cut at the seam is heard whole once
OVERLAP_SEC = 5.0
#: a scene talked over longer than this is split, so a passage is never far from its time
SPLIT_SEC = 75.0
#: speech characters per section before it is split, whatever the time
SPLIT_CHARS = 900

STAMP = re.compile(r"\[(\d{2}):([0-5]\d):([0-5]\d)\]")


def stamp(seconds: float) -> str:
    """`HH:MM:SS` for seconds (floored)."""
    whole = max(0, int(seconds))
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def short_stamp(seconds: float) -> str:
    """`M:SS` under an hour, `H:MM:SS` past it — for people, not for parsing."""
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def seconds_of(text: str) -> float | None:
    """Seconds of the first `[HH:MM:SS]` in text, or None."""
    match = STAMP.search(text or "")
    if match is None:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))


def audio_pieces(duration: float, piece: float = PIECE_SEC,
                 overlap: float = OVERLAP_SEC) -> list[tuple[float, float]]:
    """`(start, length)` pieces covering `[0, duration]`, each overlapping the next."""
    if duration <= 0:
        return []
    out: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        length = min(piece, duration - start)
        out.append((round(start, 3), round(length, 3)))
        if start + length >= duration:
            break
        start += piece - overlap
    # a sliver at the end is folded into the piece before it
    if len(out) > 1 and out[-1][1] < 2 * overlap:
        last_start, _ = out[-2]
        out[-2:] = [(last_start, round(duration - last_start, 3))]
    return out


@dataclass(slots=True)
class Said:
    start: float
    end: float
    text: str


def merge_said(pieces: list[tuple[float, list[dict]]], overlap: float = OVERLAP_SEC) -> list[Said]:
    """Segments of every piece on one timeline. Piece k owns from the middle of its overlap
    with piece k-1 to the middle of its overlap with piece k+1, so the seam's words appear once.

    `pieces`: `(piece_start, segments)` in order, segments as Whisper gives them
    (`start`/`end` relative to the piece, `text`)."""
    starts = [start for start, _ in pieces]
    out: list[Said] = []
    for k, (start, segments) in enumerate(pieces):
        lo = start + overlap / 2 if k else float("-inf")
        hi = starts[k + 1] + overlap / 2 if k + 1 < len(starts) else float("inf")
        for segment in segments:
            text = " ".join(str(segment.get("text") or "").split())
            if not text:
                continue
            try:
                begin = start + float(segment.get("start") or 0.0)
                end = start + float(segment.get("end") or segment.get("start") or 0.0)
            except (TypeError, ValueError):
                continue
            if segment.get("whole"):
                # a piece's text without timings (`transcribe.segments_of`): it is the piece
                begin = max(begin, lo)
            elif not lo <= begin < hi:
                continue
            if out and text == out[-1].text and begin - out[-1].end < 2:
                # Whisper repeats a line at a seam or in silence
                continue
            out.append(Said(round(begin, 2), round(max(end, begin), 2), text))
    out.sort(key=lambda s: s.start)
    return out


@dataclass(slots=True)
class Shown:
    """What one kept frame showed: `kind` (slide, code, diagram, screen, person, other),
    the text on it, a one-line description, and the thumbnail index."""

    t: float
    kind: str = ""
    text: str = ""
    about: str = ""
    frame: int | None = None


@dataclass(slots=True)
class Section:
    start: float
    end: float
    said: list[Said] = field(default_factory=list)
    shown: Shown | None = None

    @property
    def speech(self) -> str:
        return " ".join(s.text for s in self.said).strip()


def sections(duration: float, shown: list[Shown], said: list[Said],
             split_sec: float = SPLIT_SEC, split_chars: int = SPLIT_CHARS) -> list[Section]:
    """Scenes in time order holding their speech; the first scene starts at 0.

    A scene's speech runs from its frame to the next frame. A long one is split at speech
    segment boundaries (`split_sec`, `split_chars`); the parts after the first carry no
    picture of their own (the screen did not change)."""
    duration = max(duration, max((s.end for s in said), default=0.0))
    frames = sorted(shown, key=lambda s: s.t)
    cuts = [0.0, *(f.t for f in frames if f.t > 0)]
    first = frames[0] if frames and frames[0].t <= 0 else None
    pictures = [first, *(f for f in frames if f.t > 0)]
    scenes = [Section(start=cut, end=nxt, shown=picture) for cut, nxt, picture in
              zip(cuts, [*cuts[1:], duration], pictures, strict=True)]
    k = 0
    for line in said:
        while k + 1 < len(scenes) and line.start >= scenes[k + 1].start:
            k += 1
        scenes[k].said.append(line)
    out: list[Section] = []
    for scene in scenes:
        part = Section(start=scene.start, end=scene.end, shown=scene.shown)
        chars = 0
        for line in scene.said:
            if part.said and (line.start - part.start >= split_sec or chars >= split_chars):
                part.end = line.start
                out.append(part)
                part = Section(start=line.start, end=scene.end)
                chars = 0
            part.said.append(line)
            chars += len(line.text)
        out.append(part)
    # a scene nobody spoke over and nothing was read on stays out
    return [s for s in out if s.said or (s.shown and (s.shown.text or s.shown.about))]


_KIND_KO = {
    "slide": "슬라이드",
    "code": "코드",
    "diagram": "도식",
    "chart": "도표",
    "screen": "화면",
    "document": "문서",
    "person": "사람",
    "other": "",
}


def shown_line(shown: Shown) -> str:
    """「(화면) 슬라이드 — 설명 | 글자: …」, or "" when nothing was read."""
    kind = _KIND_KO.get(shown.kind, "")
    about = " ".join(shown.about.split())
    head = " — ".join(p for p in (kind, about) if p)
    text = " ".join(shown.text.split())
    if not head and not text:
        return ""
    line = "(화면) " + (head or "화면")
    if text:
        line += f" | 글자: {text}"
    return line


def render(name: str, duration: float, parts: list[Section], *, said_any: bool,
           shown_any: bool) -> str:
    """The file's text (see the module docstring)."""
    sources = [what for what, has in (("말", said_any), ("화면", shown_any)) if has]
    what = "·".join(sources) or "내용 없음"
    head = f"# {name} — 영상 {stamp(duration)} ({what}을 시간 순서로 옮김)"
    blocks = [head]
    for part in parts:
        lines = [f"[{stamp(part.start)}]"]
        if part.shown is not None and (line := shown_line(part.shown)):
            lines.append(line)
        if part.speech:
            lines.append(f"(말) {part.speech}")
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def segments(parts: list[Section]) -> list[dict]:
    """Structured form for citations and the outline (`StoredFile.media["segments"]`)."""
    out = []
    for part in parts:
        out.append({
            "start": round(part.start, 2),
            "end": round(part.end, 2),
            "said": part.speech[:4000],
            "shown": shown_line(part.shown)[5:].strip() if part.shown else "",
            "frame": part.shown.frame if part.shown else None,
        })
    return out


# ---- frames: near-duplicates and sampling ------------------------------------------------


def dhash(pixels: list[int], width: int = 9, height: int = 8) -> int:
    """Difference hash of a `width`×`height` grayscale picture (row-major pixel values)."""
    bits = 0
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        for x in range(width - 1):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


#: hashes this close are the same picture (a cursor, a video's compression)
SAME_PICTURE = 6


def thin(times: list[float], cap: int) -> list[float]:
    """At most `cap` times, kept evenly across the list (first and last kept)."""
    if cap <= 0:
        return []
    if len(times) <= cap:
        return list(times)
    if cap == 1:
        return [times[0]]
    step = (len(times) - 1) / (cap - 1)
    return [times[round(i * step)] for i in range(cap)]


def frame_cap(duration: float, per_minute: float, ceiling: int) -> int:
    """Frames read for a video this long: `per_minute` for short ones, never past `ceiling`."""
    return max(1, min(ceiling, int(round(max(duration, 60.0) / 60.0 * per_minute))))


__all__ = [
    "OVERLAP_SEC",
    "PIECE_SEC",
    "STAMP",
    "Said",
    "Section",
    "Shown",
    "audio_pieces",
    "dhash",
    "distance",
    "frame_cap",
    "merge_said",
    "render",
    "seconds_of",
    "sections",
    "segments",
    "short_stamp",
    "stamp",
    "thin",
]
