"""This replica's live load (open streams, request durations), sampled per minute."""

from __future__ import annotations

import socket
import time
from collections import deque

#: short container id = replica
REPLICA = f"api · {socket.gethostname()[:6]}"


class Counters:
    def __init__(self) -> None:
        self.streams = 0
        self.stream_peak = 0
        self.requests = 0
        self.errors = 0
        # bounded: latest 5000 per minute
        self.durations: deque[float] = deque(maxlen=5000)

    def open_stream(self) -> None:
        self.streams += 1
        self.stream_peak = max(self.stream_peak, self.streams)

    def close_stream(self) -> None:
        self.streams = max(self.streams - 1, 0)

    def finish(self, seconds: float, status: int) -> None:
        self.requests += 1
        if status >= 500:
            self.errors += 1
        self.durations.append(seconds)

    def drain(self) -> dict[str, float]:
        """Values for the minute just ended; resets counts."""
        ordered = sorted(self.durations)
        out = {
            "streams": float(self.streams),
            "streams_peak": float(self.stream_peak),
            "requests": float(self.requests),
            "errors_5xx": float(self.errors),
        }
        if ordered:
            out["http_p95_ms"] = 1000 * ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        self.requests = self.errors = 0
        self.stream_peak = self.streams
        self.durations.clear()
        return out


counters = Counters()


class LoadMiddleware:
    """Pure ASGI: streamed responses counted to the last byte."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        state = {"status": 0, "stream": False}

        async def watch(message):
            if message.get("type") == "http.response.start":
                state["status"] = int(message.get("status") or 0)
                for key, value in message.get("headers") or []:
                    if key.lower() == b"content-type" and value.startswith(b"text/event-stream"):
                        state["stream"] = True
                        counters.open_stream()
                        break
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except BaseException:
            state["status"] = state["status"] or 500
            raise
        finally:
            if state["stream"]:
                counters.close_stream()
            else:
                counters.finish(time.perf_counter() - started, state["status"] or 500)
