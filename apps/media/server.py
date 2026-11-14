"""The media worker: stock ffmpeg/ffprobe behind a small HTTP API, for video understanding.

ffmpeg runs only as an unmodified separate program in this container (its own licence, see
NOTICE); nothing here links it. The API process asks for one step at a time and does the rest
(transcription, reading frames, the time-coded document) itself:

    GET  /health
    POST /probe   {key}                                  -> {duration, width, height, ...}
    POST /audio   {key, start, duration}                 -> audio/flac, 16 kHz mono
    POST /scenes  {key, threshold, min_gap, max_gap, max_frames}
                                                          -> {frames: [{t, score}], seconds}
    POST /frame   {key, t, width}                        -> image/jpeg

`key` is a storage key under MEDIA_ROOT (the API's file storage, mounted read-only). The
container joins only an internal network, holds no secrets and writes nothing to disk.
Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.realpath(os.environ.get("MEDIA_ROOT", "/srv/data/files"))
#: ffmpeg runs at once; more requests wait (each step is short, the API retries)
JOBS = max(1, int(os.environ.get("MEDIA_JOBS", "2")))
#: decoder/encoder threads per ffmpeg run
THREADS = max(1, int(os.environ.get("MEDIA_THREADS", "2")))
#: longest video accepted, seconds
MAX_SECONDS = float(os.environ.get("MEDIA_MAX_SECONDS", str(4 * 3600)))
#: one ffmpeg run's wall-clock ceiling, seconds
STEP_TIMEOUT = float(os.environ.get("MEDIA_STEP_TIMEOUT", "900"))
#: longest audio piece one /audio call returns, seconds
MAX_PIECE = 30 * 60
MAX_BODY = 16 * 1024
#: scratch for audio pieces (a tmpfs in compose; the root file system is read-only)
TMP = os.environ.get("MEDIA_TMP", tempfile.gettempdir())

log = logging.getLogger("media")


def _one_line(value: object) -> str:
    """A value for a log line: line breaks and control characters out (no forged lines)."""
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    return "".join(ch if ch.isprintable() else " " for ch in text)[:500]


_SLOTS = threading.BoundedSemaphore(JOBS)


class Refused(Exception):
    """A request this service will not run; answered 4xx with the reason."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def path_of(key: object) -> str:
    """The file for a storage key; refuses anything outside ROOT or missing."""
    if not isinstance(key, str) or not key or key.startswith("/") or "\x00" in key:
        raise Refused(400, "bad_key")
    path = os.path.realpath(os.path.join(ROOT, key))
    if not path.startswith(ROOT + os.sep):
        raise Refused(400, "bad_key")
    if not os.path.isfile(path):
        raise Refused(404, "not_found")
    return path


def _number(body: dict, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(body.get(name, default))
    except (TypeError, ValueError):
        raise Refused(400, f"bad_{name}") from None
    if value != value:  # NaN
        raise Refused(400, f"bad_{name}")
    return min(max(value, low), high)


def run(args: list[str], *, timeout: float = STEP_TIMEOUT, slot: bool = True) -> bytes:
    """One ffmpeg/ffprobe run inside a slot (`slot=False`: the caller holds one), at low
    priority; stdout bytes."""
    with _SLOTS if slot else contextlib.nullcontext():
        started = time.monotonic()
        try:
            done = subprocess.run(
                ["nice", "-n", "10", *args], capture_output=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise Refused(504, "timeout") from None
        log.info("%s %.1fs rc=%d", _one_line(args[0]), time.monotonic() - started,
                 done.returncode)
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        log.warning("%s failed: %s", _one_line(args[0]), _one_line(" | ".join(tail)))
        raise Refused(422, "unreadable")
    return done.stdout


def probe(path: str) -> dict:
    raw = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", path], timeout=60)
    info = json.loads(raw or b"{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format") or {}

    def seconds(value: object) -> float:
        try:
            return max(0.0, float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    duration = seconds(fmt.get("duration")) or max(
        seconds((video or {}).get("duration")), seconds((audio or {}).get("duration")))
    return {
        "duration": round(duration, 3),
        "start": round(seconds(fmt.get("start_time")), 3),
        "hasVideo": video is not None,
        "hasAudio": audio is not None,
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "videoCodec": str((video or {}).get("codec_name") or ""),
        "audioCodec": str((audio or {}).get("codec_name") or ""),
        "format": str(fmt.get("format_name") or ""),
        "maxSeconds": MAX_SECONDS,
    }


def audio(path: str, body: dict) -> bytes:
    """A 16 kHz mono FLAC piece: what Whisper reads, small enough for its upload limit.

    Written to a file, not a pipe: a piped FLAC states no length in its header, and Whisper
    (vLLM) then reads it as endless and refuses it."""
    start = _number(body, "start", 0, 0, MAX_SECONDS)
    length = _number(body, "duration", 600, 1, MAX_PIECE)
    with tempfile.TemporaryDirectory(dir=TMP) as folder:
        out = os.path.join(folder, "piece.flac")
        run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-threads", str(THREADS), "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", path, "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
             "-c:a", "flac", out])
        with open(out, "rb") as handle:
            return handle.read()


#: changes are measured on edge maps (ffmpeg's edgedetect at 320×180, averaged down to
#: 80×45): text and slide shapes are crisp edges, a speaker moving is mostly soft ones.
#: Measured on three lectures and a talking head: a slide change moves ≥ 5 % of the cells
#: past PIXEL_STEP, a speaker's motion over five seconds 2–3 % (p50–p90).
SMALL_W, SMALL_H = 80, 45
SMALL = SMALL_W * SMALL_H
EDGES = (f"scale=320:180,format=gray,edgedetect=low=0.08:high=0.25,"
         f"scale={SMALL_W}:{SMALL_H}:flags=area")
PIXEL_STEP = 48
#: frames decoded side by side (seeks, or segments of a full pass)
SCAN_PARALLEL = max(1, int(os.environ.get("MEDIA_SCAN_PARALLEL", "4")))
#: keyframes are used instead of a full decode for pictures at least this tall, when they
#: come at least every KEY_GAP seconds (a slide shown for less than that may be missed)
KEY_HEIGHT = int(os.environ.get("MEDIA_KEYFRAME_MIN_HEIGHT", "900"))
KEY_GAP = float(os.environ.get("MEDIA_KEYFRAME_MAX_GAP", "6"))


def changed(a: bytes, b: bytes) -> float:
    """Share of edge-map cells that differ by more than PIXEL_STEP."""
    step = PIXEL_STEP
    count = 0
    for x, y in zip(a, b, strict=False):
        if x - y > step or y - x > step:
            count += 1
    return count / max(1, min(len(a), len(b)))


def keyframes(path: str) -> list[float]:
    """Keyframe times from the container's packet index (no decoding)."""
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "packet=pts_time,flags", "-of", "csv=p=0", path], timeout=300, slot=False)
    times = []
    for line in out.decode("ascii", "replace").splitlines():
        stamp, _, flags = line.partition(",")
        if "K" in flags:
            try:
                times.append(float(stamp))
            except ValueError:
                continue
    return sorted(times)


def _small_at(path: str, at: float) -> bytes:
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "1",
                "-ss", f"{at:.3f}", "-i", path, "-map", "0:v:0", "-frames:v", "1", "-vf",
                EDGES, "-f", "rawvideo", "pipe:1"],
               timeout=120, slot=False)


def _small_segment(path: str, start: float, length: float, fps: float) -> list[bytes]:
    raw = run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads", "1",
               "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path, "-map", "0:v:0",
               "-an", "-sn", "-dn", "-vf", f"fps={fps:g},{EDGES}",
               "-f", "rawvideo", "pipe:1"], slot=False)
    return [raw[i:i + SMALL] for i in range(0, len(raw) - SMALL + 1, SMALL)]


def sample(path: str, info: dict, fps: float) -> tuple[list[tuple[float, bytes]], str]:
    """Small grey frames over the whole video and how they were read.

    Tall pictures with frequent keyframes (a 1080p recording) are read at their keyframes —
    one seek each, side by side, about a fifth of a full decode; otherwise the video is
    decoded at `fps` in SCAN_PARALLEL segments side by side."""
    duration = float(info["duration"])
    pool = ThreadPoolExecutor(max_workers=SCAN_PARALLEL)
    try:
        if info.get("height", 0) >= KEY_HEIGHT:
            keys = [t for t in keyframes(path) if t <= duration]
            gaps = sorted(b - a for a, b in zip(keys, keys[1:], strict=False))
            if len(keys) >= 3 and gaps[len(gaps) // 2] <= KEY_GAP \
                    and keys[0] <= KEY_GAP and duration - keys[-1] <= 2 * KEY_GAP:
                offset = float(info.get("start") or 0.0)
                pictures = list(pool.map(lambda t: _small_at(path, t - offset), keys))
                return [(max(0.0, t - offset), p) for t, p in zip(keys, pictures, strict=True)
                        if len(p) == SMALL], "keyframes"
        parts = max(1, min(SCAN_PARALLEL, int(duration // 60) or 1))
        edges = [duration * i / parts for i in range(parts + 1)]
        segments = list(pool.map(
            lambda i: _small_segment(path, edges[i], edges[i + 1] - edges[i], fps),
            range(parts)))
    finally:
        pool.shutdown(wait=True)
    frames: list[tuple[float, bytes]] = []
    # the fps filter's frame k may be a source frame from up to half an interval later (it
    # takes the nearest); half an interval on, a seek to the time lands on that picture
    late = 0.5 / fps
    for i, pictures in enumerate(segments):
        frames += [(edges[i] + k / fps + late, p) for k, p in enumerate(pictures)]
    return frames, "decode"


def scenes(path: str, body: dict) -> dict:
    """Times where the picture changes.

    A frame is a change when more than `threshold` of its pixels differ from the last kept
    frame; the frame kept for it is the first one after the change settles (a transition, a
    slide building up). A stretch longer than `max_gap` without a change gets a frame anyway
    (a talking head, a slow scroll); past `max_frames` the kept times are thinned, changes
    first. The first frame is kept."""
    info = probe(path)
    duration = float(info["duration"])
    if not info["hasVideo"]:
        return {"frames": [], "seconds": 0.0, "mode": ""}
    if duration > MAX_SECONDS:
        raise Refused(413, "too_long")
    threshold = _number(body, "threshold", 0.04, 0.001, 0.9)
    min_gap = _number(body, "min_gap", 2, 0.5, 600)
    max_gap = _number(body, "max_gap", 90, 5, 3600)
    max_frames = int(_number(body, "max_frames", 120, 1, 1000))
    fps = _number(body, "fps", 1, 0.1, 5)
    started = time.monotonic()
    # one slot for the whole pass: its decoders run side by side inside it
    with _SLOTS:
        frames, mode = sample(path, info, fps)
    found = detect(frames, threshold)
    return {"frames": pick(found, duration, min_gap, max_gap, max_frames),
            "seconds": round(time.monotonic() - started, 2), "mode": mode,
            "sampled": len(frames)}


def detect(frames: list[tuple[float, bytes]], threshold: float) -> list[tuple[float, float]]:
    """`(time, score)` of each change against the last kept frame, taken once it settles."""
    if not frames:
        return []
    found: list[tuple[float, float]] = []
    kept = frames[0][1]
    i = 1
    while i < len(frames):
        score = changed(kept, frames[i][1])
        if score <= threshold:
            i += 1
            continue
        # wait for the picture to stop moving (at most three samples)
        j = i
        while j + 1 < len(frames) and j - i < 3 \
                and changed(frames[j][1], frames[j + 1][1]) > threshold:
            j += 1
        found.append((frames[j][0], round(changed(kept, frames[j][1]), 4)))
        kept = frames[j][1]
        i = j + 1
    return found


def pick(found: list[tuple[float, float]], duration: float, min_gap: float, max_gap: float,
         max_frames: int) -> list[dict]:
    """Kept frame times (see `scenes`)."""
    kept: list[tuple[float, float]] = [(0.0, 1.0)]
    for t, score in sorted(found):
        if t - kept[-1][0] >= min_gap:
            kept.append((t, score))
        elif kept[-1][0] > 0:
            # two changes close together: the later, settled one stands for both
            kept[-1] = (t, max(score, kept[-1][1]))
    filled: list[tuple[float, float]] = []
    ends = [*(t for t, _ in kept[1:]), max(duration - 1.0, 0.0)]
    for (t, score), end in zip(kept, ends, strict=True):
        filled.append((t, score))
        gap = end - t
        if gap > max_gap:
            steps = int(gap // max_gap)
            for k in range(1, steps + 1):
                filled.append((t + gap * k / (steps + 1), 0.0))
    if len(filled) > max_frames:
        # the biggest changes first, then evenly over time
        changes = sorted((f for f in filled if f[1] > 0), key=lambda f: -f[1])
        if len(changes) >= max_frames:
            filled = changes[:max_frames]
        else:
            fillers = sorted(f for f in filled if f[1] == 0)
            room = max_frames - len(changes)
            step = len(fillers) / room if room else 0
            filled = changes + [fillers[int(i * step)] for i in range(room)]
    return [{"t": round(t, 2), "score": round(score, 3)} for t, score in sorted(filled)]


def frame(path: str, body: dict) -> bytes:
    at = _number(body, "t", 0, 0, MAX_SECONDS)
    width = int(_number(body, "width", 1280, 64, 1920))
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads",
                str(THREADS), "-ss", f"{at:.3f}", "-i", path, "-map", "0:v:0", "-frames:v", "1",
                "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", "-c:v", "mjpeg",
                "-f", "image2", "pipe:1"], timeout=120)


class Handler(BaseHTTPRequestHandler):
    server_version = "kloudchat-media"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)

    def _send(self, status: int, data: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def do_GET(self) -> None:
        if self.path == "/health":
            ok = subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
            self._json(200 if ok else 503, {"ok": ok, "jobs": JOBS})
        else:
            self._json(404, {"detail": "not_found"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise Refused(413, "body_too_large")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise Refused(400, "bad_json") from None
            if not isinstance(body, dict):
                raise Refused(400, "bad_json")
            path = path_of(body.get("key"))
            if self.path == "/probe":
                self._json(200, probe(path))
            elif self.path == "/audio":
                self._send(200, audio(path, body), "audio/flac")
            elif self.path == "/scenes":
                self._json(200, scenes(path, body))
            elif self.path == "/frame":
                self._send(200, frame(path, body), "image/jpeg")
            else:
                raise Refused(404, "not_found")
        except Refused as exc:
            self._json(exc.status, {"detail": exc.reason})
        except Exception:
            log.exception("media request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": "failed"})


def main() -> None:
    logging.basicConfig(level=os.environ.get("MEDIA_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("MEDIA_PORT", "8300"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    log.info("media worker on :%d, root %s, %d jobs x %d threads", port, ROOT, JOBS, THREADS)
    server.serve_forever()


if __name__ == "__main__":
    main()
