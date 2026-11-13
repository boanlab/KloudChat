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

import json
import logging
import os
import re
import subprocess
import threading
import time
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

log = logging.getLogger("media")
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


def run(args: list[str], *, timeout: float = STEP_TIMEOUT) -> bytes:
    """One ffmpeg/ffprobe run inside a slot, at low priority; stdout bytes."""
    with _SLOTS:
        started = time.monotonic()
        try:
            done = subprocess.run(
                ["nice", "-n", "10", *args], capture_output=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise Refused(504, "timeout") from None
        log.info("%s %.1fs rc=%d", args[0], time.monotonic() - started, done.returncode)
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        log.warning("%s failed: %s", args[0], " | ".join(tail))
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
    """A 16 kHz mono FLAC piece: what Whisper reads, small enough for its upload limit."""
    start = _number(body, "start", 0, 0, MAX_SECONDS)
    length = _number(body, "duration", 600, 1, MAX_PIECE)
    return run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                "-threads", str(THREADS), "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                "-i", path, "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000",
                "-c:a", "flac", "-f", "flac", "pipe:1"])


_PTS = re.compile(rb"pts_time:([0-9.]+)")
_SCORE = re.compile(rb"lavfi\.scene_score=([0-9.]+)")


def scenes(path: str, body: dict) -> dict:
    """Times where the picture changes, from a low-resolution pass at `fps` frames a second.

    A change closer than `min_gap` to the last kept one is dropped (a slide build, a cursor);
    a stretch longer than `max_gap` without one gets a frame anyway (a talking head, a slow
    scroll); past `max_frames` the kept times are thinned evenly. The first frame is kept."""
    info = probe(path)
    duration = float(info["duration"])
    if not info["hasVideo"]:
        return {"frames": [], "seconds": 0.0}
    if duration > MAX_SECONDS:
        raise Refused(413, "too_long")
    threshold = _number(body, "threshold", 0.12, 0.01, 0.9)
    min_gap = _number(body, "min_gap", 4, 0.5, 600)
    max_gap = _number(body, "max_gap", 90, 5, 3600)
    max_frames = int(_number(body, "max_frames", 120, 1, 1000))
    fps = _number(body, "fps", 1, 0.1, 5)
    started = time.monotonic()
    out = run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-threads",
               str(THREADS), "-i", path, "-map", "0:v:0", "-an", "-sn", "-dn", "-vf",
               f"fps={fps:g},scale=320:-2,select='gt(scene\\,{threshold:g})',"
               "metadata=print:file=-", "-f", "null", "-"])
    offset = float(info.get("start") or 0.0)
    found: list[tuple[float, float]] = []
    at: float | None = None
    for line in out.splitlines():
        if (m := _PTS.search(line)) is not None:
            at = max(0.0, float(m.group(1)) - offset)
        elif (m := _SCORE.search(line)) is not None and at is not None:
            found.append((at, float(m.group(1))))
            at = None
    return {"frames": pick(found, duration, min_gap, max_gap, max_frames),
            "seconds": round(time.monotonic() - started, 2)}


def pick(found: list[tuple[float, float]], duration: float, min_gap: float, max_gap: float,
         max_frames: int) -> list[dict]:
    """Kept frame times (see `scenes`)."""
    kept: list[tuple[float, float]] = [(0.0, 1.0)]
    for t, score in sorted(found):
        if t - kept[-1][0] >= min_gap:
            kept.append((t, score))
        elif score > kept[-1][1] and kept[-1][0] > 0:
            # two changes close together: the stronger one stands for both
            kept[-1] = (t, score)
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
        # scene changes before fillers, then evenly over time
        ranked = sorted(filled, key=lambda f: -f[1])
        changes = [f for f in ranked if f[1] > 0]
        if len(changes) >= max_frames:
            filled = changes
        step = len(sorted(filled)) / max_frames
        ordered = sorted(filled)
        filled = [ordered[int(i * step)] for i in range(max_frames)]
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

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 — quieter access log
        log.debug(fmt, *args)

    def _send(self, status: int, data: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            ok = subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
            self._json(200 if ok else 503, {"ok": ok, "jobs": JOBS})
        else:
            self._json(404, {"detail": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
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
        except Exception:  # noqa: BLE001 — a bad file never takes the service down
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
