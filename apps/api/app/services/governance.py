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

import hashlib
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import jwt
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.chat import Message
from app.models.governance import Governance
from app.models.user import utcnow

log = logging.getLogger(__name__)

_TTL = 15.0
_cache: dict = {"at": 0.0, "value": None}

DETECTOR_VERSION = "privacy-detector-v1"
POLICY_VERSION = "external-data-guard-v1"
_TOKEN_TTL_SEC = 300


class GovernanceUnavailable(RuntimeError):
    """The authoritative policy row could not be read for an egress decision."""


@dataclass(frozen=True, slots=True)
class _Detection:
    category: str
    label: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Finding:
    """Value-free aggregate safe to return to a browser or audit log."""

    category: str
    source: str
    count: int

    def wire(self) -> dict[str, Any]:
        return {"category": self.category, "source": self.source, "count": self.count}


# Narrow candidates followed by format/checksum validation. False positives on
# this path make people disable the feature, so semantic name/address detection
# belongs in a later opt-in classifier rather than this deterministic baseline.
_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}(?![\w.-])", re.I)
_KR_MOBILE = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016-9][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_KR_LANDLINE = re.compile(
    r"(?<!\d)(?:\(0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])\)\s*|"
    r"0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])[- ])\d{3,4}[- ]\d{4}(?!\d)"
)
# NANP area and exchange codes cannot start with 0 or 1. Separators or a
# parenthesized area code are required so arbitrary ten-digit identifiers do
# not become phone numbers.
_NANP = re.compile(
    r"(?<!\d)(?:\+?1[ .-])?(?:\([2-9]\d{2}\)\s*|[2-9]\d{2}[ .-])"
    r"[2-9]\d{2}[ .-]\d{4}(?!\d)"
)
# Candidate followed by digit-count and parenthesis validation. This covers
# E.164 as well as the spaced/hyphenated form people actually paste.
_INTERNATIONAL_PHONE = re.compile(r"(?<![\w+])\+[1-9][0-9 .()-]{5,28}[0-9](?!\w)")
_RRN = re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Candidate only; ``ipaddress`` below is the validator. Requiring at least two
# colons avoids treating ordinary prose containing one colon as an address.
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_LEGACY_RRN = re.compile(r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}\b")
_LEGACY_CARD = re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b")
_LEGACY_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("api_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api_key", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b")),
    ("api_key", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "api_key",
        re.compile(
            r"\b(?:aws[_ -]*secret(?:[_ -]*access)?[_ -]*key|secretaccesskey)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}[\"']?",
            re.I,
        ),
    ),
    (
        "api_key",
        re.compile(
            r"\bAccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}",
            re.I,
        ),
    ),
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
]

_LABELS = {
    "email": "[이메일]",
    "phone": "[전화번호]",
    "government_id": "[주민번호]",
    "payment_card": "[카드번호]",
    "ip_address": "[IP주소]",
    "api_key": "[API키]",
    "jwt": "[JWT]",
    "private_key": "[개인키]",
}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _valid_rrn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 13:
        return False
    # The seventh digit identifies both sex and century. Calendar validation
    # rejects impossible dates such as 990231 that pass the checksum by chance.
    century = 1900 if digits[6] in "1256" else 2000
    try:
        date(century + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    check = (11 - sum(int(n) * w for n, w in zip(digits[:12], weights, strict=True)) % 11) % 10
    return check == int(digits[-1])


def _valid_luhn(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _valid_international_phone(value: str) -> bool:
    digits = _digits(value)
    if not 8 <= len(digits) <= 15:
        return False
    if value.count("(") != value.count(")") or value.count("(") > 2:
        return False
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _detections(text: str) -> list[_Detection]:
    candidates: list[_Detection] = []

    def add(category: str, match: re.Match[str]) -> None:
        candidates.append(_Detection(category, _LABELS[category], match.start(), match.end()))

    for match in _EMAIL.finditer(text):
        add("email", match)
    for match in _KR_MOBILE.finditer(text):
        add("phone", match)
    for match in _KR_LANDLINE.finditer(text):
        add("phone", match)
    for match in _NANP.finditer(text):
        add("phone", match)
    for match in _INTERNATIONAL_PHONE.finditer(text):
        if _valid_international_phone(match.group()):
            add("phone", match)
    for match in _RRN.finditer(text):
        if _valid_rrn(match.group()):
            add("government_id", match)
    for match in _CARD.finditer(text):
        if _valid_luhn(match.group()):
            add("payment_card", match)
    for match in _IPV4.finditer(text):
        try:
            ipaddress.IPv4Address(match.group())
        except ipaddress.AddressValueError:
            continue
        add("ip_address", match)
    for match in _IPV6.finditer(text):
        try:
            ipaddress.IPv6Address(match.group())
        except ipaddress.AddressValueError:
            continue
        add("ip_address", match)
    for category, pattern in _SECRETS:
        for match in pattern.finditer(text):
            add(category, match)

    # Sort once, then sweep once. Same-start longer matches win (a private-key
    # block can contain token-like fragments); later overlapping candidates are
    # skipped. This is O(n log n), including finding-heavy adversarial input.
    accepted: list[_Detection] = []
    covered_until = -1
    for item in sorted(candidates, key=lambda d: (d.start, -(d.end - d.start), d.category)):
        if item.start < covered_until:
            continue
        accepted.append(item)
        covered_until = item.end
    return accepted


async def current(force: bool = False) -> Governance:
    """Returns a short-lived policy snapshot for non-authorizing work.

    UI hints and background retention may tolerate this process-local cache.
    Any decision that can let content leave the service must instead call
    :func:`current_for_egress` so a different worker's revocation is immediate.
    """
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    try:
        async with SessionLocal() as db:
            row = await db.get(Governance, "default")
            policy = row or Governance()
    except Exception as exc:  # noqa: BLE001
        # Preserve non-authorizing UI/background hints from a stale known
        # policy, but never display guard-off/raw-delivery after a read failure.
        # Egress callers do not use this fallback; ``current_for_egress`` raises.
        log.warning("governance unreadable, enabling external data guard: %s", exc)
        stale = _cache["value"]
        if stale is not None:
            return stale.model_copy(
                update={
                    "external_data_guard": True,
                    "allow_user_raw_external": False,
                }
            )
        return Governance(external_data_guard=True, allow_user_raw_external=False)
    _cache.update(at=now, value=policy)
    return policy


async def current_for_egress() -> Governance:
    """Returns the policy that may authorize an outbound model request.

    The ordinary 15-second cache is process-local. In a multi-worker server,
    invalidating it after an admin update only reaches the worker that handled
    that update; another worker could otherwise keep allowing raw external
    delivery. Egress authorization therefore pays for one primary-key read on
    every turn (and raw-default preference save).

    A read failure is different from a policy that says "guard on": masking,
    prohibited-intent categories and the legacy upper bound are all unknown.
    Neither a synthesized default nor a stale cache can authorize even a
    strict-local turn in that state, so callers must surface a stable 503.
    """
    now = time.monotonic()
    try:
        async with SessionLocal() as db:
            row = await db.get(Governance, "default")
            policy = row or Governance()
    except Exception as exc:  # noqa: BLE001 — every DB failure denies egress
        log.error(
            "authoritative governance read failed; egress denied (%s)",
            type(exc).__name__,
        )
        raise GovernanceUnavailable from exc
    _cache.update(at=now, value=policy)
    return policy


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


def mask(text: str) -> tuple[str, int]:
    """`(redacted text, how many)`. Counting is what makes the audit line useful."""
    hits = _detections(text)
    return _render_masked(text, hits), len(hits)


def _render_masked(text: str, hits: list[_Detection]) -> str:
    """Renders all replacements with one join rather than repeated copies."""
    if not hits:
        return text
    parts: list[str] = []
    cursor = 0
    for item in hits:
        parts.extend((text[cursor : item.start], item.label))
        cursor = item.end
    parts.append(text[cursor:])
    return "".join(parts)


def _legacy_detections(text: str) -> list[_Detection]:
    candidates = _detections(text)
    for category, pattern in (
        ("government_id", _LEGACY_RRN),
        ("payment_card", _LEGACY_CARD),
        ("email", _LEGACY_EMAIL),
    ):
        for match in pattern.finditer(text):
            item = _Detection(category, _LABELS[category], match.start(), match.end())
            candidates.append(item)
    accepted: list[_Detection] = []
    covered_until = -1
    for item in sorted(candidates, key=lambda d: (d.start, -(d.end - d.start), d.category)):
        if item.start < covered_until:
            continue
        accepted.append(item)
        covered_until = item.end
    return accepted


def mask_legacy(text: str) -> tuple[str, int]:
    """Compatibility mask used by the existing organisation-wide policy."""
    hits = _legacy_detections(text)
    return _render_masked(text, hits), len(hits)


def findings(sources: dict[str, str | list[str]], *, legacy: bool = False) -> list[Finding]:
    """Scans source-labelled text and aggregates without retaining values."""
    counts: dict[tuple[str, str], int] = {}
    for source, raw in sources.items():
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            detector = _legacy_detections if legacy else _detections
            for hit in detector(value or ""):
                key = (hit.category, source)
                counts[key] = counts.get(key, 0) + 1
    return [
        Finding(category=category, source=source, count=count)
        for (category, source), count in sorted(counts.items())
    ]


def envelope_digest(sources: dict[str, str | list[str]]) -> str:
    canonical = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def issue_decision_token(
    *, user_id: str, session_id: str, requested_models: list[str], digest: str
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "typ": "privacy-decision",
            "sub": user_id,
            "sid": session_id,
            "models": requested_models,
            "envelope": digest,
            "iat": now,
            "exp": now + _TOKEN_TTL_SEC,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def verify_decision_token(
    token: str | None,
    *,
    user_id: str,
    session_id: str,
    requested_models: list[str],
    digest: str,
) -> bool:
    if not token:
        return False
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return False
    return (
        claims.get("typ") == "privacy-decision"
        and claims.get("sub") == user_id
        and claims.get("sid") == session_id
        and claims.get("models") == requested_models
        and claims.get("envelope") == digest
    )


def finding_metadata(rows: list[Finding]) -> dict[str, Any]:
    return {
        "policyVersion": POLICY_VERSION,
        "detectorVersion": DETECTOR_VERSION,
        "findings": [row.wire() for row in rows],
    }


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


__all__ = [
    "DETECTOR_VERSION",
    "POLICY_VERSION",
    "Finding",
    "GovernanceUnavailable",
    "blocked_by",
    "current",
    "current_for_egress",
    "envelope_digest",
    "finding_metadata",
    "findings",
    "invalidate",
    "issue_decision_token",
    "mask",
    "mask_legacy",
    "sweep_expired",
    "verify_decision_token",
]
