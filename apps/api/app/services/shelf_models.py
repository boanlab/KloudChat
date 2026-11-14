"""Which model writes each 지식 서가 output — the one place to change it.

Per output kind: `strictModel` where internal material must stay inside (the administrator's
rule, on by default) or no outside model is allowed; `model` where it may leave; `effort`
the reasoning effort (`none`, `high`). Defaults come from the 2026-11-13 evaluations: the
Super Agent's (flash with thinking on scored 8.5 on long and complex tasks against the 27B's
6-7; DeepSeek V4.1 Flash with reasoning on is its escalation pick, used here where material
may leave) and the knowledge shelf's own comparison below.
`KNOWLEDGE_MODELS` (JSON, the same shape, per kind) overrides any of it.
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

#: 2026-11-13, ten lecture notes, Sonnet judge, 27B against flash: flash won the briefing
#: (7 vs 6), the glossary (7 vs 5) and the quiz (8 vs 7). With thinking on, flash ran past a
#: 14k-token room on every long document attempt (briefing, glossary) and answered only
#: without it, so documents are written without thinking; a quiz finished thinking in about
#: 70 s and is the better for it. Thinking that runs out is answered again without it.
DEFAULTS: dict[str, dict[str, str]] = {
    "study_guide": dict(_FLASH),
    "faq": dict(_FLASH),
    "briefing": dict(_FLASH),
    "timeline": dict(_FLASH),
    "glossary": dict(_FLASH),
    "mindmap": dict(_FLASH),
    "audio": dict(_FLASH),
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
