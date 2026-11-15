"""Recent failures of slow self-hosted writers, so a turn does not wait on a doomed attempt.

A writer that failed a turn (the gateway refused it, the server was unreachable, the stream
broke, or it showed nothing within its patience) is left out of Super Agent picks for a while:
briefly when the gateway only said it was busy (`strict_local_unavailable`: vLLM has requests
waiting), longer when it looked down (DeepSeek V4 Flash runs across both GB10 nodes, so either
node down takes it away). Per process; a restart forgets, and the next turn finds out again.
"""

from __future__ import annotations

import time

#: seconds a model is skipped after the gateway said it was busy
BUSY_SECONDS = 60.0
#: seconds a model is skipped after it looked down (unreachable, broken stream, no answer)
DOWN_SECONDS = 300.0

_until: dict[str, float] = {}


def busy_refusal(error: str) -> bool:
    """Whether a stream error is the gateway's concurrency gate, not a fault."""
    return error.startswith("upstream_503") or "strict_local_unavailable" in error


def mark_failed(model_id: str, error: str = "") -> float:
    """Records a failure of `model_id`; returns how many seconds it is skipped."""
    seconds = BUSY_SECONDS if busy_refusal(error) else DOWN_SECONDS
    _until[model_id] = max(_until.get(model_id, 0.0), time.monotonic() + seconds)
    return seconds


def mark_ok(model_id: str) -> None:
    """A finished answer clears the mark."""
    _until.pop(model_id, None)


def healthy(model_id: str) -> bool:
    until = _until.get(model_id)
    if until is None:
        return True
    if until <= time.monotonic():
        _until.pop(model_id, None)
        return True
    return False


def unhealthy() -> frozenset[str]:
    """Models to leave out of picks right now."""
    return frozenset(model_id for model_id in list(_until) if not healthy(model_id))


def reset() -> None:
    """Forget every mark (tests)."""
    _until.clear()
