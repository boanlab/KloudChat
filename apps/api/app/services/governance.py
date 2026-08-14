"""Policy that runs, applied where it can take effect.

* **PII masking** rewrites the text on its way to the model, and before the
  write. Redacting afterwards leaves the original in the database.
* **Intent filtering** refuses the turn before any model is called, so a
  blocked request costs nothing and produces no partial answer.
* **Retention** clears message bodies past their age. Rows and metadata stay —
  the audit trail is what must not be edited.

Read through a short cache, since this is consulted on every turn.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SessionLocal
from app.models.chat import Message
from app.models.governance import Governance
from app.models.user import utcnow

log = logging.getLogger(__name__)

_TTL = 15.0
_cache: dict = {"at": 0.0, "value": None}

#: Patterns redacted before text leaves for a third party. Narrow on purpose:
#: a rule that eats ordinary numbers gets the whole feature switched off.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Korean resident registration number — 6 digits, hyphen, 7 digits, with a
    # plausible birth date in the first half.
    ("[주민번호]", re.compile(r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}\b")),
    # Card numbers: four groups of four.
    ("[카드번호]", re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b")),
    # Korean mobile numbers, hyphenated or not.
    ("[전화번호]", re.compile(r"\b01[016-9][- ]?\d{3,4}[- ]?\d{4}\b")),
    ("[이메일]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
]


async def current(force: bool = False) -> Governance:
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    try:
        async with SessionLocal() as db:
            row = await db.get(Governance, "default")
            policy = row or Governance()
    except Exception as exc:  # noqa: BLE001 — a DB blip must not block every turn
        log.warning("governance unreadable, treating as off: %s", exc)
        return _cache["value"] or Governance()
    _cache.update(at=now, value=policy)
    return policy


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


def mask(text: str) -> tuple[str, int]:
    """`(redacted text, how many)`. Counting is what makes the audit line useful."""
    hits = 0
    for label, pattern in _PATTERNS:
        text, n = pattern.subn(label, text)
        hits += n
    return text, hits


def blocked_by(text: str, categories: list[str]) -> str | None:
    """The first configured category the text matches, or None.

    Substring matching on the category name, not a model call: the words an
    administrator types are the words they expect to catch.
    """
    lowered = text.lower()
    for category in categories:
        needle = str(category).strip().lower()
        if needle and needle in lowered:
            return str(category)
    return None


async def sweep_expired(db: AsyncSession) -> int:
    """Blanks message bodies past the retention window. Returns how many.

    Bodies only: the model, token counts and credits are what the usage and
    audit screens are built from. Retention is about content, not about erasing
    that anything happened.
    """
    policy = await current(force=True)
    if policy.retention_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=policy.retention_days)
    rows = (
        await db.exec(
            select(Message).where(
                col(Message.created_at) < cutoff,
                col(Message.content) != "",
            )
        )
    ).all()
    for message in rows:
        message.content = ""
        message.variants = None
        db.add(message)
    if rows:
        log.info("retention: cleared %d message bodies older than %s", len(rows), cutoff.date())
    return len(rows)


__all__ = ["blocked_by", "current", "invalidate", "mask", "sweep_expired"]
