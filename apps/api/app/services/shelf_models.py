"""Which model writes each 지식 서가 output — the one place to change it.

Per output kind: `strictModel` where internal material must stay inside (administrator rule, on by
default) or no outside model is allowed; `model` where it may leave; `effort` the reasoning effort
(`none`, `high`). `KNOWLEDGE_MODELS` (JSON, same shape, per kind) overrides any of it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

_FLASH = {"strictModel": "strict-local/qwen3.8-flash", "effort": "none",
          "model": "deepseek/deepseek-v4.1-flash"}
_FLASH_THINKING = {**_FLASH, "effort": "high"}

#: Long documents are written without thinking (thinking overruns the token room); the quiz benefits
#: from it. Thinking that runs out is retried without it.
DEFAULTS: dict[str, dict[str, str]] = {
    "study_guide": dict(_FLASH),
    "faq": dict(_FLASH),
    "briefing": dict(_FLASH),
    "timeline": dict(_FLASH),
    "glossary": dict(_FLASH),
    "mindmap": dict(_FLASH),
    "quiz": dict(_FLASH_THINKING),
    "flashcards": dict(_FLASH_THINKING),
    #: source guides: short summaries of one file, many of them; no thinking needed
    "guide": {"strictModel": "strict-local/qwen3.8-flash", "effort": "none",
              "model": "strict-local/qwen3.8-flash"},
    #: whether a suggested question is answered in its passages (two judges with the guide)
    "answerable": dict(_FLASH_THINKING),
    #: per-sentence wording verdicts beside the writer's own
    "proofread": {"strictModel": "strict-local/qwen3.8-flash", "effort": "none",
                  "model": "strict-local/qwen3.8-flash"},
}

#: next strict-local picks when a kind's model is not served or not allowed
STRICT_FALLBACKS = ("strict-local/qwen3.8-flash", "strict-local/qwen3.8-27b")


def table() -> dict[str, dict[str, str]]:
    """The defaults with `KNOWLEDGE_MODELS` laid over them."""
    merged = {kind: dict(row) for kind, row in DEFAULTS.items()}
    raw = (settings.knowledge_models or "").strip()
    if not raw:
        return merged
    try:
        given = json.loads(raw)
    except ValueError:
        log.warning("KNOWLEDGE_MODELS is not JSON; the defaults stand")
        return merged
    for kind, row in (given or {}).items() if isinstance(given, dict) else ():
        if kind in merged and isinstance(row, dict):
            merged[kind].update({k: str(v) for k, v in row.items()
                                 if k in ("model", "strictModel", "effort")})
    return merged


def pick(kind: str) -> dict[str, Any]:
    return table().get(kind) or table()["study_guide"]


__all__ = ["DEFAULTS", "STRICT_FALLBACKS", "pick", "table"]
